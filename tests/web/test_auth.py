"""The four-digit PIN in front of the dashboard (§43).

The contract:

* a visitor without a session sees the PIN page and nothing else — no figure, no navigation;
* the very first visit chooses the PIN, which is stored hashed in a local file;
* a correct PIN earns a cookie valid 30 days, and logging out ends it at once;
* five wrong PINs lock the login for 15 minutes;
* the PIN appears in no page, no URL, no log record and no cookie.

The PINs below are test fixtures, not secrets: nothing here is deployed.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from tests.web import seed

from tradingagent.web.app import create_app
from tradingagent.web.auth import (
    LOCKOUT,
    MAX_ATTEMPTS,
    SESSION_COOKIE,
    SESSION_DAYS,
    Outcome,
    PinGate,
    PinStore,
    valid_pin,
)

PIN = "4827"
OTHER_PIN = "1593"

# Every entry point, including the SSE feed and the exports: none is reachable without a
# session. `/healthz`, `/login` and `/logout` are the deliberate exceptions, tested below.
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
    "/export/trades.csv",
    "/export/trades.json",
    "/export/performance.json",
    "/export/equity.svg",
)

# Markers that only exist behind the PIN: a navigation entry, a figure.
DASHBOARD_MARKERS = ("Vue d'ensemble", "16.75", "Positions")


class Clock:
    """A clock the test can move: the cookie and the lockout both depend on time."""

    def __init__(self) -> None:
        self.current: datetime = seed.NOW

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **delta: float) -> None:
        self.current += timedelta(**delta)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def pin_file(tmp_path: Path) -> Path:
    return tmp_path / "data" / "web_pin.json"


def _client(engine: Engine, pin_file: Path, clock: Clock) -> TestClient:
    """A protected client that never follows a redirect: the 303 is the tested behaviour."""
    return TestClient(create_app(engine, now=clock, pin_file=pin_file), follow_redirects=False)


def _choose(client: TestClient, pin: str = PIN) -> None:
    response = client.post("/login", data={"pin": pin, "confirm": pin})
    assert response.status_code == 303


@pytest.fixture
def chosen(engine: Engine, populated: seed.Seeded, pin_file: Path, clock: Clock) -> TestClient:
    """A protected dashboard whose PIN is already chosen, with no session yet."""
    del populated
    _choose(_client(engine, pin_file, clock))
    return _client(engine, pin_file, clock)


@pytest.fixture
def signed_in(chosen: TestClient) -> TestClient:
    assert chosen.post("/login", data={"pin": PIN}).status_code == 303
    return chosen


# ---------------------------------------------------------------------------------------
# Without a session: the PIN page, and nothing of the dashboard.
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_every_entry_point_refuses_a_visitor_without_a_session(
    chosen: TestClient, path: str
) -> None:
    response = chosen.get(path)
    if path.startswith("/events"):
        assert response.status_code == 401  # an EventSource cannot follow a redirect
    else:
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
    for marker in DASHBOARD_MARKERS:
        assert marker not in response.text, f"{path} leaked {marker!r}"


def test_a_write_verb_without_a_session_is_refused(chosen: TestClient) -> None:
    assert chosen.post("/").status_code == 401
    assert chosen.put("/positions").status_code == 401


def test_the_login_page_reveals_nothing_of_the_dashboard(chosen: TestClient) -> None:
    page = chosen.get("/login")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    for marker in DASHBOARD_MARKERS:
        assert marker not in page.text
    assert 'name="pin"' in page.text
    assert 'name="confirm"' not in page.text  # the PIN already exists: no choosing again


def test_the_liveness_probe_stays_reachable_for_the_supervisor(chosen: TestClient) -> None:
    response = chosen.get("/healthz")
    assert response.status_code == 200
    assert response.json()["read_only"] is True


def test_the_static_assets_of_the_login_page_stay_reachable(chosen: TestClient) -> None:
    assert chosen.get("/static/nexagold.png").status_code == 200


# ---------------------------------------------------------------------------------------
# First visit: choosing the PIN.
# ---------------------------------------------------------------------------------------


def test_the_first_visit_asks_to_choose_a_pin(engine: Engine, pin_file: Path, clock: Clock) -> None:
    client = _client(engine, pin_file, clock)
    page = client.get("/login")
    assert page.status_code == 200
    assert 'name="confirm"' in page.text
    assert client.get("/").headers["location"] == "/login"


@pytest.mark.parametrize("bad", ["", "123", "12345", "abcd", "12 4", "١٢٣٤", "12.4"])
def test_a_pin_that_is_not_four_ascii_digits_is_refused(
    engine: Engine, pin_file: Path, clock: Clock, bad: str
) -> None:
    client = _client(engine, pin_file, clock)
    response = client.post("/login", data={"pin": bad, "confirm": bad})
    assert response.status_code == 400
    assert not pin_file.exists()
    assert valid_pin(bad) is False


def test_two_different_codes_are_not_saved(engine: Engine, pin_file: Path, clock: Clock) -> None:
    client = _client(engine, pin_file, clock)
    response = client.post("/login", data={"pin": PIN, "confirm": OTHER_PIN})
    assert response.status_code == 400
    assert not pin_file.exists()


def test_choosing_the_pin_signs_the_operator_in(
    engine: Engine, populated: seed.Seeded, pin_file: Path, clock: Clock
) -> None:
    del populated
    client = _client(engine, pin_file, clock)
    response = client.post("/login", data={"pin": PIN, "confirm": PIN})
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert client.get("/").status_code == 200


def test_the_pin_is_stored_hashed_and_never_in_clear(
    engine: Engine, pin_file: Path, clock: Clock
) -> None:
    _choose(_client(engine, pin_file, clock))
    text = pin_file.read_text(encoding="utf-8")
    assert PIN not in text
    assert set(json.loads(text)) == {"salt", "hash", "key"}
    assert PinStore(pin_file).matches(PIN) is True
    assert PinStore(pin_file).matches(OTHER_PIN) is False


# ---------------------------------------------------------------------------------------
# Signing in, the 30-day session, signing out.
# ---------------------------------------------------------------------------------------


def test_a_correct_pin_opens_every_page_and_the_sse_feed(signed_in: TestClient) -> None:
    for path in PROTECTED_PATHS:
        assert signed_in.get(path).status_code in {200, 404}, path
    assert signed_in.get("/login").headers["location"] == "/"  # already in: no second prompt


def test_the_cookie_is_httponly_strict_and_lasts_thirty_days(chosen: TestClient) -> None:
    response = chosen.post("/login", data={"pin": PIN})
    header = response.headers["set-cookie"].lower()
    assert f"{SESSION_COOKIE}=" in header
    assert "httponly" in header
    assert "samesite=strict" in header
    assert f"max-age={SESSION_DAYS * 24 * 3600}" in header
    assert SESSION_DAYS == 30


def test_the_cookie_never_carries_the_pin(chosen: TestClient) -> None:
    response = chosen.post("/login", data={"pin": PIN})
    assert PIN not in response.headers["set-cookie"]
    assert PIN not in response.headers["location"]


def test_the_session_expires_after_thirty_days(signed_in: TestClient, clock: Clock) -> None:
    clock.advance(days=29, hours=23)
    assert signed_in.get("/").status_code == 200
    clock.advance(hours=2)
    response = signed_in.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_logging_out_ends_the_session_and_asks_the_pin_again(signed_in: TestClient) -> None:
    out = signed_in.post("/logout")
    assert out.status_code == 303
    assert out.headers["location"] == "/login"
    assert signed_in.get("/").headers["location"] == "/login"
    assert signed_in.post("/login", data={"pin": PIN}).status_code == 303
    assert signed_in.get("/").status_code == 200


def test_logging_out_is_harmless_without_a_session(chosen: TestClient) -> None:
    assert chosen.post("/logout").status_code == 303


def test_every_page_offers_a_logout_button(signed_in: TestClient) -> None:
    body = signed_in.get("/").text
    assert 'action="/logout"' in body
    assert 'method="post"' in body


def test_a_forged_or_tampered_cookie_is_refused(signed_in: TestClient, clock: Clock) -> None:
    real = signed_in.cookies.get(SESSION_COOKIE)
    assert real is not None
    stamp, _, signature = real.partition(".")
    far_future = str(int(clock.current.timestamp()) + 10 * 365 * 86400)
    forged = (
        "",
        ".",
        "0" * 64,
        PIN,
        "deadbeef",
        f"{far_future}.{signature}",  # a valid signature moved onto a longer expiry
        f"{stamp}.{'0' * 64}",
        f"{stamp}.{signature[:-1]}",
        "-1." + signature,
        "9" * 40 + "." + signature,
    )
    for value in forged:
        signed_in.cookies.clear()
        response = signed_in.get("/", headers={"Cookie": f"{SESSION_COOKIE}={value}"})
        assert response.status_code == 303, value
    # A hostile non-ASCII value is a refusal, never a 500 from the constant-time comparison.
    hostile = signed_in.get("/", headers={b"Cookie": SESSION_COOKIE.encode() + b"=1.cl\xe9"})
    assert hostile.status_code == 303


def test_choosing_a_new_pin_invalidates_every_older_cookie(
    engine: Engine, populated: seed.Seeded, pin_file: Path, clock: Clock
) -> None:
    del populated
    first = _client(engine, pin_file, clock)
    _choose(first)
    stale = first.cookies.get(SESSION_COOKIE)
    assert stale is not None

    pin_file.unlink()  # the documented way to reset the PIN
    second = _client(engine, pin_file, clock)
    _choose(second, OTHER_PIN)

    old = second.get("/", headers={"Cookie": f"{SESSION_COOKIE}={stale}"})
    # `second` also holds its own fresh cookie; send only the stale one.
    other = _client(engine, pin_file, clock)
    refused = other.get("/", headers={"Cookie": f"{SESSION_COOKIE}={stale}"})
    assert old.status_code in {200, 303}
    assert refused.status_code == 303


# ---------------------------------------------------------------------------------------
# Wrong PINs and the lockout.
# ---------------------------------------------------------------------------------------


def test_a_wrong_pin_is_refused_and_counts_the_tries_left(chosen: TestClient) -> None:
    response = chosen.post("/login", data={"pin": OTHER_PIN})
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert f"{MAX_ATTEMPTS - 1} essais" in response.text
    assert chosen.get("/").status_code == 303


@pytest.mark.parametrize("bad", ["", "12", "abcd", "١٢٣٤", "4827 ", "48271"])
def test_a_malformed_pin_is_just_a_wrong_pin(chosen: TestClient, bad: str) -> None:
    assert chosen.post("/login", data={"pin": bad}).status_code == 401


def test_five_wrong_pins_lock_the_login_even_for_the_right_one(
    chosen: TestClient, clock: Clock
) -> None:
    for _ in range(MAX_ATTEMPTS - 1):
        assert chosen.post("/login", data={"pin": OTHER_PIN}).status_code == 401
    locked = chosen.post("/login", data={"pin": OTHER_PIN})
    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) == int(LOCKOUT.total_seconds())

    # The right PIN does not get through while locked.
    still = chosen.post("/login", data={"pin": PIN})
    assert still.status_code == 429
    assert "set-cookie" not in still.headers

    clock.advance(minutes=14)
    assert chosen.post("/login", data={"pin": PIN}).status_code == 429
    clock.advance(minutes=2)
    assert chosen.post("/login", data={"pin": PIN}).status_code == 303


def test_a_good_pin_resets_the_failure_count(chosen: TestClient) -> None:
    for _ in range(MAX_ATTEMPTS - 1):
        chosen.post("/login", data={"pin": OTHER_PIN})
    assert chosen.post("/login", data={"pin": PIN}).status_code == 303
    for _ in range(MAX_ATTEMPTS - 1):
        assert chosen.post("/login", data={"pin": OTHER_PIN}).status_code == 401


def test_the_gate_reports_each_outcome(pin_file: Path, clock: Clock) -> None:
    store = PinStore(pin_file)
    store.choose(PIN)
    gate = PinGate(store, clock)
    assert gate.attempt(OTHER_PIN).outcome is Outcome.WRONG
    assert gate.attempt(PIN).outcome is Outcome.GRANTED
    for _ in range(MAX_ATTEMPTS):
        last = gate.attempt(OTHER_PIN)
    assert last.outcome is Outcome.LOCKED
    assert last.retry_after > 0


# ---------------------------------------------------------------------------------------
# Hygiene.
# ---------------------------------------------------------------------------------------


def test_the_pin_never_reaches_a_log_record_or_a_page(
    caplog: pytest.LogCaptureFixture, chosen: TestClient
) -> None:
    with caplog.at_level(logging.DEBUG):
        chosen.post("/login", data={"pin": OTHER_PIN})
        chosen.post("/login", data={"pin": PIN})
        bodies = [chosen.get(path).text for path in ("/", "/trades", "/system", "/login")]
    assert OTHER_PIN not in caplog.text and PIN not in caplog.text
    for body in bodies:
        assert OTHER_PIN not in body and f'value="{PIN}"' not in body


def test_the_pin_travels_in_the_body_never_in_the_url(chosen: TestClient) -> None:
    response = chosen.post("/login", data={"pin": PIN})
    assert PIN not in response.headers["location"]
    # A GET that carries a PIN in the query string does not sign anyone in.
    fresh = TestClient(chosen.app, follow_redirects=False)
    assert fresh.get("/login", params={"pin": PIN}).status_code == 200
    assert fresh.get("/", params={"pin": PIN}).status_code == 303


def test_an_oversized_form_is_not_buffered_and_is_refused(chosen: TestClient) -> None:
    response = chosen.post("/login", data={"pin": PIN + "0" * 5000})
    assert response.status_code == 401


AI_PROBE = "/ai-lab/test"  # the test button: one model call, no database write


def test_the_only_post_routes_are_the_two_session_routes_and_the_ai_probe(
    engine: Engine, pin_file: Path, clock: Clock
) -> None:
    """The PIN adds two POST routes, the AI test button a third. None writes to the database."""
    app = create_app(engine, now=clock, pin_file=pin_file)
    posts = {
        route.path
        for route in app.routes
        if isinstance(route, APIRoute) and "POST" in (route.methods or set())
    }
    assert posts == {"/login", "/logout", AI_PROBE}
    for route in app.routes:
        if isinstance(route, APIRoute):
            assert set(route.methods or set()) <= {"GET", "HEAD", "POST"}
            if route.path not in {"/login", "/logout", AI_PROBE}:
                assert set(route.methods or set()) <= {"GET", "HEAD"}, route.path


def test_without_a_pin_file_the_app_is_open_for_embedders(
    engine: Engine, populated: seed.Seeded
) -> None:
    del populated
    app = create_app(engine)
    assert app.state.access_protected is False
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/login").status_code == 404


def test_the_app_states_whether_it_is_protected(engine: Engine, pin_file: Path) -> None:
    assert create_app(engine, pin_file=pin_file).state.access_protected is True


# ---------------------------------------------------------------------------------------
# Hardening from the security review.
# ---------------------------------------------------------------------------------------


def test_an_unreadable_pin_file_never_reopens_the_setup(chosen: TestClient, pin_file: Path) -> None:
    pin_file.write_text("{not json", encoding="utf-8")
    page = chosen.get("/login")
    assert 'name="confirm"' not in page.text  # still the login form, not "choose a PIN"
    refused = chosen.post("/login", data={"pin": OTHER_PIN, "confirm": OTHER_PIN})
    assert refused.status_code == 503
    assert pin_file.read_text(encoding="utf-8") == "{not json"


def test_a_post_from_another_origin_is_refused(chosen: TestClient) -> None:
    for headers in (
        {"Origin": "http://evil.example"},
        {"Origin": "http://testserver:9999"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ):
        response = chosen.post("/login", data={"pin": PIN}, headers=headers)
        assert response.status_code == 403, headers
        assert "set-cookie" not in response.headers
    ok = chosen.post(
        "/login",
        data={"pin": PIN},
        headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
    )
    assert ok.status_code == 303


def test_a_cookie_with_a_huge_timestamp_is_a_refusal_not_a_500(chosen: TestClient) -> None:
    response = chosen.get("/", headers={"Cookie": f"{SESSION_COOKIE}={'9' * 5000}.ab"})
    assert response.status_code == 303


def test_the_ai_probe_needs_a_session(engine: Engine, pin_file: Path, clock: Clock) -> None:
    app = create_app(engine, now=clock, pin_file=pin_file)
    with TestClient(app) as client:
        assert client.post(AI_PROBE).status_code == 401
