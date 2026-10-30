"""The real HTTP client: standard library, confined to a thread, bounded in time and size.

The standard library is enough for a handful of public GET requests and keeps the dependency
list (and the lock file) untouched. Blocking I/O never runs on the event loop.

Three limits keep a hostile or broken host from doing more than failing one fetch:

- a redirect is followed only to https and only to a host of a known source (`known_hosts`):
  a feed that starts answering `302 -> http://169.254.169.254/` is refused, not obeyed;
- the whole read has a wall-clock deadline, checked between chunks, because the socket timeout
  only bounds one wait and a body trickled a byte at a time would otherwise never end;
- an internal error is stored as its type and its first line, never as a raw message.
"""

import asyncio
import http.client
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import IO, Any, Protocol
from urllib.parse import urlsplit

from tradingagent.news.faireconomy import NEXT_WEEK_URL, THIS_WEEK_URL
from tradingagent.news.ports import HttpError, HttpResponse
from tradingagent.news.rss import FEEDS
from tradingagent.resilience import one_line

USER_AGENT = "TradingAgent/0.1 (+economic-calendar; read-only)"
MAX_BYTES = 2_000_000
#: Seconds the whole of one request may last, connection and body together.
DEADLINE_SECONDS = 30.0
CHUNK_BYTES = 64 * 1024
MAX_REDIRECTS = 3


def known_hosts() -> frozenset[str]:
    """The hosts of the configured sources: the only places a redirect may lead."""
    urls = (THIS_WEEK_URL, NEXT_WEEK_URL, *(feed.url for feed in FEEDS))
    return frozenset(host for url in urls if (host := urlsplit(url).hostname))


class RestrictedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only when it stays on https and on a known source host."""

    max_redirections = MAX_REDIRECTS

    def __init__(self, allowed_hosts: frozenset[str]) -> None:
        self._allowed_hosts = allowed_hosts

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        try:
            parts = urlsplit(newurl)
            host = (parts.hostname or "").lower()
        except ValueError:
            host = ""
            parts = urlsplit("")
        if parts.scheme != "https" or host not in self._allowed_hosts:
            raise HttpError(
                f"redirection refusée vers {parts.scheme or '?'}://{host or '?'} (HTTP {code})"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _Opener(Protocol):
    def open(self, request: urllib.request.Request, *, timeout: float) -> Any: ...


class UrllibHttpClient:
    def __init__(
        self,
        user_agent: str = USER_AGENT,
        max_bytes: int = MAX_BYTES,
        *,
        allowed_hosts: frozenset[str] | None = None,
        deadline: float = DEADLINE_SECONDS,
        chunk_bytes: int = CHUNK_BYTES,
        clock: Callable[[], float] = time.monotonic,
        opener: _Opener | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._max_bytes = max_bytes
        self._deadline = deadline
        self._chunk_bytes = chunk_bytes
        self._clock = clock
        # No file, ftp or clear-text handler: only what a public https feed needs.
        director = urllib.request.OpenerDirector()
        for handler in (
            urllib.request.ProxyHandler(),
            urllib.request.UnknownHandler(),
            urllib.request.HTTPSHandler(),
            RestrictedRedirectHandler(allowed_hosts or known_hosts()),
            urllib.request.HTTPDefaultErrorHandler(),
            urllib.request.HTTPErrorProcessor(),
        ):
            director.add_handler(handler)
        self._opener = director
        self._open: _Opener = opener if opener is not None else _DirectorAdapter(director)

    async def get(self, url: str, *, timeout: float) -> HttpResponse:
        if urlsplit(url).scheme != "https":
            raise HttpError(f"only https is allowed, got {url!r}")
        return await asyncio.to_thread(self._get, url, timeout)

    def _get(self, url: str, timeout: float) -> HttpResponse:
        request = urllib.request.Request(  # noqa: S310 - https only, checked in `get`
            url,
            headers={
                "User-Agent": self._user_agent,
                "Accept": "application/json, application/rss+xml, application/xml;q=0.9, */*;q=0.5",
            },
        )
        deadline = self._clock() + self._deadline
        try:
            with self._open.open(request, timeout=timeout) as response:
                body = self._read(response, url, deadline)
                return HttpResponse(response.status, body, dict(response.headers.items()))
        except urllib.error.HTTPError as error:
            # A status code is an answer, not a transport failure: the source decides what it means.
            return HttpResponse(
                error.code, self._read(error, url, deadline), dict(error.headers.items())
            )
        except HttpError:
            raise
        except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
            # Type and first line only: a urllib message can carry the whole socket error text.
            raise HttpError(one_line(error)) from error

    def _read(self, stream: Any, url: str, deadline: float) -> bytes:
        """Read the body in chunks, giving up at the size limit or at the wall-clock deadline."""
        chunks: list[bytes] = []
        size = 0
        while True:
            if self._clock() > deadline:
                raise HttpError(f"délai total dépassé ({self._deadline:.0f} s) pour {url}")
            chunk = stream.read(min(self._chunk_bytes, self._max_bytes + 1 - size))
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            size += len(chunk)
            if size > self._max_bytes:
                raise HttpError(f"response from {url} exceeds {self._max_bytes} bytes")


class _DirectorAdapter:
    """`OpenerDirector.open` takes `timeout` positionally after `data`; the seam wants a keyword."""

    def __init__(self, director: urllib.request.OpenerDirector) -> None:
        self._director = director

    def open(self, request: urllib.request.Request, *, timeout: float) -> Any:
        return self._director.open(request, timeout=timeout)
