"""The real HTTP client's guard rails: where a redirect may lead, how long a read may last, and
how little of an internal error ends up in the journal."""

import asyncio
import http.client
import io
import urllib.error
import urllib.request
import urllib.response
from collections.abc import Callable
from types import TracebackType
from typing import Any

import pytest

from tradingagent.news.http import (
    DEADLINE_SECONDS,
    RestrictedRedirectHandler,
    UrllibHttpClient,
    known_hosts,
)
from tradingagent.news.ports import HttpError
from tradingagent.resilience import ONE_LINE_LIMIT

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class SlowResponse:
    """A body that arrives in small pieces, each of which costs `cost` seconds."""

    status = 200

    def __init__(self, clock: Clock, *, pieces: int, cost: float) -> None:
        self._clock = clock
        self._left = pieces
        self._cost = cost
        self.headers: dict[str, str] = {}
        self.reads = 0

    def read(self, _size: int = -1) -> bytes:
        self.reads += 1
        if self._left == 0:
            return b""
        self._left -= 1
        self._clock.now += self._cost
        return b"x" * 10

    def __enter__(self) -> "SlowResponse":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


class FakeOpener:
    def __init__(self, behaviour: Callable[[], Any]) -> None:
        self._behaviour = behaviour

    def open(self, _request: urllib.request.Request, timeout: float) -> Any:
        return self._behaviour()


def get(client: UrllibHttpClient, url: str = FEED) -> Any:
    return asyncio.run(client.get(url, timeout=5.0))


def redirect(handler: RestrictedRedirectHandler, target: str) -> urllib.request.Request | None:
    return handler.redirect_request(
        urllib.request.Request(FEED),
        None,  # type: ignore[arg-type]
        302,
        "Found",
        http.client.HTTPMessage(),
        target,
    )


# --- where a redirect may lead --------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "http://nfs.faireconomy.media/ff_calendar_thisweek.json",  # downgrade to clear text
        "file:///C:/Windows/win.ini",
        "ftp://nfs.faireconomy.media/x",
        "https://evil.example/ff_calendar_thisweek.json",  # a host that is not a known source
        "https://nfs.faireconomy.media.evil.example/x",  # a lookalike of a known one
        "https://169.254.169.254/latest/meta-data",
        "https:///nohost",
    ],
)
def test_a_redirect_to_another_scheme_or_an_unknown_host_is_refused(target: str) -> None:
    handler = RestrictedRedirectHandler(known_hosts())
    with pytest.raises(HttpError, match="redirection refusée"):
        redirect(handler, target)


def test_a_redirect_inside_the_known_hosts_is_followed() -> None:
    handler = RestrictedRedirectHandler(known_hosts())
    followed = redirect(handler, "https://nfs.faireconomy.media/other.json")
    assert followed is not None
    assert followed.full_url == "https://nfs.faireconomy.media/other.json"


def test_the_known_hosts_are_exactly_the_hosts_of_the_configured_sources() -> None:
    hosts = known_hosts()
    assert "nfs.faireconomy.media" in hosts
    assert "www.federalreserve.gov" in hosts
    assert "cointelegraph.com" in hosts
    assert "evil.example" not in hosts


def test_the_client_only_speaks_https_and_follows_the_restricted_redirects() -> None:
    opener = UrllibHttpClient()._opener
    kinds = {type(handler) for handler in opener.handlers}  # type: ignore[attr-defined]
    assert RestrictedRedirectHandler in kinds
    assert urllib.request.FileHandler not in kinds
    assert urllib.request.FTPHandler not in kinds
    assert urllib.request.HTTPHandler not in kinds  # no clear-text handler at all


# --- how long a read may last ---------------------------------------------------------------


def test_a_body_that_trickles_in_is_cut_at_the_wall_clock_deadline() -> None:
    clock = Clock()
    response = SlowResponse(clock, pieces=10_000, cost=1.0)
    client = UrllibHttpClient(
        opener=FakeOpener(lambda: response), clock=clock, deadline=30.0, chunk_bytes=10
    )
    with pytest.raises(HttpError, match="délai"):
        get(client)
    # Stopped by the clock, not by the end of an endless body, nor by the size limit.
    assert response.reads <= 35


def test_a_body_that_arrives_in_time_is_returned_whole() -> None:
    clock = Clock()
    response = SlowResponse(clock, pieces=5, cost=1.0)
    client = UrllibHttpClient(
        opener=FakeOpener(lambda: response), clock=clock, deadline=30.0, chunk_bytes=10
    )
    assert get(client).body == b"x" * 50


def test_the_default_deadline_is_bounded() -> None:
    assert 0 < DEADLINE_SECONDS <= 60


def test_the_size_limit_still_applies_to_a_body_that_is_fast() -> None:
    clock = Clock()
    response = SlowResponse(clock, pieces=100, cost=0.0)
    client = UrllibHttpClient(
        opener=FakeOpener(lambda: response), clock=clock, max_bytes=50, chunk_bytes=10
    )
    with pytest.raises(HttpError, match="exceeds"):
        get(client)


# --- what an internal error leaves behind ---------------------------------------------------


def test_a_transport_error_keeps_its_type_and_one_short_line() -> None:
    noisy = "getaddrinfo failed\n" + "Traceback (most recent call last): " + "y" * 600

    def boom() -> Any:
        raise urllib.error.URLError(noisy)

    with pytest.raises(HttpError) as raised:
        get(UrllibHttpClient(opener=FakeOpener(boom)))
    message = str(raised.value)
    assert "URLError" in message
    assert "\n" not in message
    assert len(message) <= ONE_LINE_LIMIT
    assert "yyyy" not in message


def test_a_protocol_error_from_the_standard_library_is_an_http_error_too() -> None:
    def boom() -> Any:
        raise http.client.BadStatusLine("garbage")

    with pytest.raises(HttpError, match="BadStatusLine"):
        get(UrllibHttpClient(opener=FakeOpener(boom)))


def test_a_non_https_request_is_refused_before_any_connection() -> None:
    def never() -> Any:
        raise AssertionError("must not connect")

    with pytest.raises(HttpError, match="https"):
        get(UrllibHttpClient(opener=FakeOpener(never)), "http://nfs.faireconomy.media/x")


def test_an_actual_redirect_response_to_an_unknown_host_is_refused_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []

    def fake_https_open(
        _self: urllib.request.HTTPSHandler, req: urllib.request.Request
    ) -> urllib.response.addinfourl:
        opened.append(req.full_url)
        headers = http.client.HTTPMessage()
        headers["Location"] = "https://evil.example/steal"
        response = urllib.response.addinfourl(io.BytesIO(b""), headers, req.full_url, 302)
        response.msg = "Found"  # type: ignore[attr-defined]
        return response

    monkeypatch.setattr(urllib.request.HTTPSHandler, "https_open", fake_https_open)
    with pytest.raises(HttpError, match="redirection refusée"):
        get(UrllibHttpClient())
    assert opened == [FEED]  # the second request was never made
