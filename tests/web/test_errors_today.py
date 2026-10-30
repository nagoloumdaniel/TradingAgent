"""Every list of errors starts the day clean: today's rows by default, history on request.

The brief, verbatim in spirit: clear the errors so the dashboard can start from zero, or show
them day by day, "sauf si les erreurs précédentes sont recherchées ou triées". The journal is
append-only and immutable, so nothing is deleted: the *window* every error list reads through
is one UTC day, and a date, the arrows, a search or "tout l'historique" widens it again.

Seeded (see ``tests/web/seed.py``), with the clock frozen on ``NOW`` = 2026-10-07 12:00 UTC:
today carries a warning (``broker_disconnected``), a critical
(``clock_mismatch``) and an info (``cycle``); the tests add yesterday's rows themselves.
"""

import re
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert
from tests.web.seed import NOW, AiCallSpec, Seeded, a_failed_call, add_ai_calls

from tradingagent.core.states import Severity
from tradingagent.storage.models import SystemEventRow
from tradingagent.web import paging, queries
from tradingagent.web.app import create_app

TODAY = paging.resolve_day(None, NOW)
YESTERDAY = paging.resolve_day("2026-10-06", NOW)
EVERYTHING = paging.resolve_day("all", NOW)


def _old_error(engine: Engine, *, kind: str = "feed_lost", days: int = 1, hours: int = 3) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(SystemEventRow).values(
                kind=kind,
                severity=Severity.CRITICAL,
                detail={"attempts": 9},
                occurred_at=NOW - timedelta(days=days, hours=hours),
            )
        )


def _tbody(body: str, identifier: str) -> str:
    match = re.search(rf'<tbody id="{identifier}">(.*?)</tbody>', body, re.S)
    assert match, f"no #{identifier} in the page"
    return match.group(1)


# --- system events ----------------------------------------------------------------------


def test_recent_events_default_to_the_whole_history_for_callers_that_do_not_choose(
    engine: Engine, populated: Seeded
) -> None:
    _old_error(engine)

    kinds = {event.kind for event in queries.recent_events(engine, minimum=Severity.WARNING)}

    assert kinds == {"broker_disconnected", "clock_mismatch", "feed_lost"}


def test_recent_events_can_be_read_through_one_day(engine: Engine, populated: Seeded) -> None:
    _old_error(engine)

    today = queries.recent_events(engine, minimum=Severity.WARNING, day=TODAY)
    yesterday = queries.recent_events(engine, minimum=Severity.WARNING, day=YESTERDAY)

    assert {event.kind for event in today} == {"broker_disconnected", "clock_mismatch"}
    assert [event.kind for event in yesterday] == ["feed_lost"]


def test_the_error_page_counts_and_words_the_window(engine: Engine, populated: Seeded) -> None:
    _old_error(engine)

    today = queries.event_page(
        engine, minimum=Severity.WARNING, day=TODAY, noun="erreur", feminine=True
    )
    history = queries.event_page(
        engine, minimum=Severity.WARNING, day=EVERYTHING, noun="erreur", feminine=True
    )

    assert today.total == 2
    assert history.total == 3
    assert today.scope == "aujourd'hui"
    assert history.scope == "tout l'historique"


def test_a_clean_day_says_so_instead_of_staying_mute(engine: Engine, populated: Seeded) -> None:
    quiet = paging.resolve_day("2026-10-01", NOW)

    page = queries.event_page(
        engine, minimum=Severity.WARNING, day=quiet, noun="erreur", feminine=True
    )

    assert page.total == 0
    assert page.empty_message == "Aucune erreur le 01/10/2026."


def test_the_window_is_carried_by_the_page_links(engine: Engine, populated: Seeded) -> None:
    for number in range(12):
        _old_error(engine, kind=f"feed_lost_{number}", hours=number % 20 + 1)

    page = queries.event_page(
        engine,
        minimum=Severity.WARNING,
        day=YESTERDAY,
        path="/system",
        page_param="epage",
    )

    assert page.total == 12
    assert "day=2026-10-06" in page.url(2)
    today = queries.event_page(engine, minimum=Severity.WARNING, day=TODAY, path="/system")
    assert "day=" not in today.url(2)


# --- the system page --------------------------------------------------------------------


def test_the_system_page_lists_only_todays_errors(engine: Engine, populated: Seeded) -> None:
    _old_error(engine, kind="feed_lost_yesterday")

    with TestClient(create_app(engine, now=lambda: NOW)) as client:
        body = client.get("/system").text

    errors = _tbody(body, "errors-body")
    assert "clock_mismatch" in errors
    assert "feed_lost_yesterday" not in errors
    assert "aujourd" in body  # the window is named next to the list


def test_a_chosen_day_reaches_that_days_errors(engine: Engine, populated: Seeded) -> None:
    _old_error(engine, kind="feed_lost_yesterday")

    with TestClient(create_app(engine, now=lambda: NOW)) as client:
        body = client.get("/system", params={"day": "2026-10-06"}).text

    errors = _tbody(body, "errors-body")
    assert "feed_lost_yesterday" in errors
    assert "clock_mismatch" not in errors


def test_a_search_reaches_the_history_without_choosing_a_day(
    engine: Engine, populated: Seeded
) -> None:
    _old_error(engine, kind="feed_lost_yesterday")

    with TestClient(create_app(engine, now=lambda: NOW)) as client:
        body = client.get("/system", params={"q": "feed_lost"}).text

    assert "feed_lost_yesterday" in _tbody(body, "errors-body")


def test_the_whole_history_is_one_link_away(engine: Engine, populated: Seeded) -> None:
    _old_error(engine, kind="feed_lost_yesterday")

    with TestClient(create_app(engine, now=lambda: NOW)) as client:
        default = client.get("/system").text
        everything = client.get("/system", params={"day": "all"}).text

    assert "day=all" in default  # the way to the history is offered
    errors = _tbody(everything, "errors-body")
    assert "feed_lost_yesterday" in errors
    assert "clock_mismatch" in errors


def test_the_error_card_counts_today_only(engine: Engine, populated: Seeded) -> None:
    _old_error(engine)

    with TestClient(create_app(engine, now=lambda: NOW)) as client:
        body = client.get("/system").text

    assert "Erreurs du jour" in body
    assert re.search(r'Erreurs du jour</div>\s*<div class="value[^"]*">2<', body)


# --- the risk page ----------------------------------------------------------------------


def test_the_risk_events_log_is_a_daily_log_too(engine: Engine, populated: Seeded) -> None:
    _old_error(engine, kind="feed_lost_yesterday")

    with TestClient(create_app(engine, now=lambda: NOW)) as client:
        today = client.get("/risk").text
        yesterday = client.get("/risk", params={"day": "2026-10-06"}).text

    assert "feed_lost_yesterday" not in today
    assert "feed_lost_yesterday" in yesterday


# --- the AI laboratory ------------------------------------------------------------------


def test_ai_failures_of_other_days_do_not_paint_today_red(
    engine: Engine, populated: Seeded
) -> None:
    add_ai_calls(
        engine,
        (
            a_failed_call(NOW - timedelta(days=1, hours=2)),
            AiCallSpec(called_at=NOW - timedelta(hours=2), verdict="approved"),
        ),
    )

    today = queries.ai_call_health(engine, day=TODAY)
    everything = queries.ai_call_health(engine, day=EVERYTHING)

    assert today.calls == 1
    assert today.failed == 0
    assert today.healthy is True
    assert today.failures == ()
    assert everything.calls == 2
    assert everything.failed == 1


def test_the_ai_page_starts_from_zero_each_day(engine: Engine, populated: Seeded) -> None:
    add_ai_calls(engine, (a_failed_call(NOW - timedelta(days=2)),))

    with TestClient(create_app(engine, now=lambda: NOW)) as client:
        body = client.get("/ai-lab").text
        past = client.get("/ai-lab", params={"day": "all"}).text

    assert "n'ont pas abouti" not in body
    assert "Insufficient Balance" not in body
    assert "1 appel(s) sur 1 n'ont pas abouti" in past
    assert "Insufficient Balance" in past


def test_a_failure_today_is_still_loud(engine: Engine, populated: Seeded) -> None:
    add_ai_calls(engine, (a_failed_call(NOW - timedelta(minutes=5)),))

    with TestClient(create_app(engine, now=lambda: NOW)) as client:
        body = client.get("/ai-lab").text

    assert "1 appel(s) sur 1 n'ont pas abouti" in body
    assert "Insufficient Balance" in body


# --- the live feed ----------------------------------------------------------------------


def test_the_live_alert_feed_never_replays_yesterday(engine: Engine, populated: Seeded) -> None:
    from tradingagent.web.sse import EventStream

    _old_error(engine, kind="feed_lost_yesterday")
    stream = EventStream(engine, now=lambda: NOW)

    frames = stream.poll(NOW, None)
    alerts = next(frame for frame in frames if frame.event == "alerts")

    assert "feed_lost_yesterday" not in alerts.data
    assert "clock_mismatch" in alerts.data


# --- the day control and the quiet states -----------------------------------------------


def test_a_quiet_ai_day_is_green_and_says_how_many_calls_exist(
    engine: Engine, populated: Seeded
) -> None:
    add_ai_calls(engine, (a_failed_call(NOW - timedelta(days=3)),))

    with TestClient(create_app(engine, now=lambda: NOW)) as client:
        body = client.get("/ai-lab").text

    assert "Aucun appel aujourd" in body
    assert "donc aucune erreur" in body


def test_the_day_arrows_keep_the_search_and_the_market(engine: Engine, populated: Seeded) -> None:
    with TestClient(create_app(engine, now=lambda: NOW)) as client:
        body = client.get("/system", params={"day": "2026-10-05", "q": "clock"}).text

    links = re.findall(r'href="(/system\?[^"]*day=[^"]*)"', body)
    assert links, "the day control renders no link"
    assert all("q=clock" in link for link in links)
    assert any("day=2026-10-04" in link for link in links)
    assert any("day=2026-10-06" in link for link in links)
