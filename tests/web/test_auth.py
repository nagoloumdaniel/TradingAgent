"""Optional access protection (§43), proved without ever putting the token in a URL.

The contract, in the brief's own terms:

* without ``TRADINGAGENT_WEB_TOKEN``, the dashboard behaves exactly as before — local,
  unauthenticated, read-only;
* with it, every page *and* the SSE feed require the token, and a request without a valid
  credential gets a 401 that reveals none of the dashboard;
* the token appears in no template, no log record and no URL.

The token below is a test fixture, not a secret: nothing here is deployed.
"""

import logging

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from tests.web import seed

from tradingagent.web.app import create_app
from tradingagent.web.auth import (
    ACCESS_ENV_VAR,
    MINIMUM_RECOMMENDED_LENGTH,
    SESSION_COOKIE,
    AccessControl,
    configured_token,
)

TOKEN = "jeton-de-test-0123456789abcdef"
OTHER_TOKEN = "un-autre-jeton-0123456789abcdef"

# Every entry point, including the SSE feed, the exports and the liveness probe: when the
# token is set, none of them is reachable without it.
PROTECTED_PATHS = (
    "/",
    "/positions",
    "/trades",
    "/trades/1",
    "/strategies",
    "/ai-lab",
    "/risk",
    "/system",
    "/reports",
    "/events?cycles=1",
    "/healthz",
    "/export/trades.csv",
    "/export/trades.json",
    "/export/performance.json",
    "/export/equity.svg",
)

# Markers that only exist behind the token: a navigation entry, the read-only tag, a figure.
DASHBOARD_MARKERS = ("TradingAgent", "lecture seule", "Vue d'ensemble", "16.75")


def _app(engine: Engine, token: str) -> TestClient:
    """A protected client that never follows a redirect: the 303 is the tested behaviour."""
    return TestClient(
        create_app(engine, now=lambda: seed.NOW, access_token=token), follow_redirects=False
    )


# ---------------------------------------------------------------------------------------
# Without the variable: nothing changes.
# ---------------------------------------------------------------------------------------


def test_without_the_variable_the_dashboard_is_open(seeded_client: TestClient) -> None:
    assert seeded_client.get("/").status_code == 200
    assert seeded_client.get("/trades/1").status_code in {200, 404}


def test_an_absent_token_leaves_no_session_route(seeded_client: TestClient) -> None:
    assert seeded_client.get("/session").status_code == 404


def test_a_blank_variable_does_not_lock_the_dashboard(
    monkeypatch: pytest.MonkeyPatch, engine: Engine, populated: seed.Seeded
) -> None:
    """An empty variable is a mistake, not a password: it must not enable protection."""
    monkeypatch.setenv(ACCESS_ENV_VAR, "   ")
    with TestClient(create_app(engine, now=lambda: seed.NOW)) as client:
        assert client.get("/").status_code == 200


def test_the_token_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, engine: Engine, populated: seed.Seeded
) -> None:
    monkeypatch.setenv(ACCESS_ENV_VAR, TOKEN)
    with TestClient(create_app(engine, now=lambda: seed.NOW)) as client:
        assert client.get("/").status_code == 401
        assert client.get("/", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


# ---------------------------------------------------------------------------------------
# With the token: a refusal that reveals nothing, and a pass that works everywhere.
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_every_entry_point_refuses_a_request_without_the_token(
    engine: Engine, populated: seed.Seeded, path: str
) -> None:
    response = _app(engine, TOKEN).get(path)
    assert response.status_code == 401, path
    assert response.headers["www-authenticate"] == "Bearer"
    for marker in DASHBOARD_MARKERS:
        assert marker not in response.text, f"{path} leaked {marker!r} in its 401"


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_every_entry_point_answers_with_a_valid_bearer_token(
    engine: Engine, populated: seed.Seeded, path: str
) -> None:
    response = _app(engine, TOKEN).get(path, headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code in {200, 404}, path
    assert TOKEN not in response.text


def test_the_sse_feed_is_protected_and_then_works(engine: Engine, populated: seed.Seeded) -> None:
    client = _app(engine, TOKEN)
    assert client.get("/events?cycles=1").status_code == 401
    streamed = client.get("/events?cycles=1", headers={"Authorization": f"Bearer {TOKEN}"})
    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert "event: positions" in streamed.text


def test_a_wrong_malformed_or_hostile_header_is_refused_quietly(
    engine: Engine, populated: seed.Seeded
) -> None:
    client = _app(engine, TOKEN)
    for header in (
        f"Bearer {OTHER_TOKEN}",
        TOKEN,  # the raw token without the scheme is not a bearer credential
        "Bearer ",
        "Basic amV0b24=",
        "",
    ):
        response = client.get("/", headers={"Authorization": header})
        assert response.status_code == 401, header
    # A non-ASCII value must be a 401, never a 500 from a constant-time comparison. It is
    # handed in as raw bytes because an HTTP header is bytes, not text.
    hostile = client.get("/", headers={b"Authorization": b"Bearer cl\xe9-accentu\xe9e"})
    assert hostile.status_code == 401


def test_the_session_cookie_is_derived_from_the_token_and_never_carries_it(
    engine: Engine, populated: seed.Seeded
) -> None:
    client = _app(engine, TOKEN)
    assert client.get("/session").status_code == 401

    minted = client.get("/session", headers={"Authorization": f"Bearer {TOKEN}"})
    assert minted.status_code == 303
    assert minted.headers["location"] == "/"
    cookie = minted.cookies.get(SESSION_COOKIE)
    assert cookie is not None
    assert TOKEN not in cookie
    assert TOKEN not in str(minted.headers)
    assert client.cookies.get(SESSION_COOKIE) == cookie

    # The browser flow — a navigation and an EventSource — now works on the cookie alone.
    assert client.get("/").status_code == 200
    assert client.get("/events?cycles=1").status_code == 200


def test_a_forged_cookie_is_refused(engine: Engine, populated: seed.Seeded) -> None:
    client = _app(engine, TOKEN)
    for value in ("", "0" * 64, TOKEN, "deadbeef"):
        response = client.get("/", headers={"Cookie": f"{SESSION_COOKIE}={value}"})
        assert response.status_code == 401, value


def test_rotating_the_token_invalidates_the_cookie(engine: Engine, populated: seed.Seeded) -> None:
    first = _app(engine, TOKEN)
    first.get("/session", headers={"Authorization": f"Bearer {TOKEN}"})
    stale = first.cookies.get(SESSION_COOKIE)
    assert stale is not None

    rotated = _app(engine, OTHER_TOKEN)
    response = rotated.get("/", headers={"Cookie": f"{SESSION_COOKIE}={stale}"})
    assert response.status_code == 401
    assert rotated.get("/", headers={"Authorization": f"Bearer {OTHER_TOKEN}"}).status_code == 200


def test_the_token_never_reaches_a_log_record(
    caplog: pytest.LogCaptureFixture, engine: Engine, populated: seed.Seeded
) -> None:
    """A short token triggers the operator warning; the warning names the variable only."""
    short = "abc123"
    with caplog.at_level(logging.DEBUG):
        client = _app(engine, short)
        assert client.get("/", headers={"Authorization": f"Bearer {short}"}).status_code == 200
        assert client.get("/").status_code == 401
    assert caplog.records, "a token below the recommended length must warn"
    assert short not in caplog.text
    assert ACCESS_ENV_VAR in caplog.text


def test_the_token_is_not_written_into_a_page(engine: Engine, populated: seed.Seeded) -> None:
    client = _app(engine, TOKEN)
    for path in ("/", "/trades", "/system"):
        body = client.get(path, headers={"Authorization": f"Bearer {TOKEN}"}).text
        assert TOKEN not in body
        assert "TRADINGAGENT_WEB_TOKEN" not in body


def test_the_token_never_travels_in_a_url(engine: Engine, populated: seed.Seeded) -> None:
    """Both credentials travel in headers or in a cookie; a redirect stays token-free."""
    client = _app(engine, TOKEN)
    response = client.get("/session", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.headers["location"] == "/"
    # The SSE and page URLs the templates build carry no credential either.
    body = client.get("/positions", headers={"Authorization": f"Bearer {TOKEN}"}).text
    assert "token" not in body.lower()
    assert "jeton" not in body.lower()


def test_protection_keeps_the_read_only_surface(engine: Engine, populated: seed.Seeded) -> None:
    """The token guards the door; it does not add a write verb behind it."""
    app = create_app(engine, access_token=TOKEN)
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    assert routes
    for route in routes:
        assert set(route.methods or set()) <= {"GET", "HEAD"}, route.path


def test_the_border_only_ever_answers_get(engine: Engine, populated: seed.Seeded) -> None:
    client = _app(engine, TOKEN)
    # No credential at all: the write verb is refused before any page is rendered.
    assert client.post("/").status_code == 401
    # With the token: the route table itself refuses the verb, as it always did.
    assert client.post("/", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 405


def test_the_app_states_whether_it_is_protected(engine: Engine, populated: seed.Seeded) -> None:
    """One flag a supervisor or an embedding script can read without guessing."""
    assert create_app(engine).state.access_protected is False
    assert create_app(engine, access_token=TOKEN).state.access_protected is True


def test_an_absent_token_is_normalised_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ACCESS_ENV_VAR, raising=False)
    assert configured_token() is None
    monkeypatch.setenv(ACCESS_ENV_VAR, "")
    assert configured_token() is None
    monkeypatch.setenv(ACCESS_ENV_VAR, "  valeur-avec-espaces  ")
    assert configured_token() == "valeur-avec-espaces"


def test_the_control_never_reprs_its_token() -> None:
    control = AccessControl(TOKEN)
    assert TOKEN not in repr(control)
    assert len(control.session_value) == 64
    assert control.session_value != TOKEN
    assert control.bearer_granted(f"bearer {TOKEN}") is True  # the scheme is case-insensitive
    assert control.bearer_granted(f"Bearer {TOKEN}") is True
    assert control.bearer_granted(f"Bearer  {TOKEN} ") is True
    assert control.bearer_granted(f"Bearer {OTHER_TOKEN}") is False
    assert control.bearer_granted(None) is False
    assert control.cookie_granted(control.session_value) is True
    assert control.cookie_granted(TOKEN) is False
    assert control.cookie_granted(None) is False
    assert MINIMUM_RECOMMENDED_LENGTH == 16
