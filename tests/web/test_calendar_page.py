"""The economic calendar page: GET /calendar, its day and month selection, and its honesty.

No network: events, analyses and fetch reports are written straight into a migrated test
database, the way the news service would have left them.
"""

from datetime import UTC, datetime, timedelta
from html import unescape

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from tests.web.seed import NOW

from tradingagent.news.brief import fallback_brief
from tradingagent.news.fallback import fallback_analysis
from tradingagent.news.model import (
    BTCUSD,
    XAUUSD,
    EconomicEvent,
    EventKind,
    Impact,
    MarketState,
    event_key,
)
from tradingagent.news.ports import EMPTY_CALENDAR, SourceReport
from tradingagent.storage.news import NewsStore
from tradingagent.web.app import create_app
from tradingagent.web.calendar_view import (
    BadCalendarRequest,
    paris_offset,
    parse_day,
    parse_month,
    to_paris,
)
from tradingagent.web.sse import fingerprint

CPI_AT = NOW + timedelta(minutes=30)  # 12:30 UTC, 14:30 in Paris (summer time)
STATES = {XAUUSD: MarketState(XAUUSD), BTCUSD: MarketState(BTCUSD)}


def event(
    title: str,
    at: datetime,
    *,
    impact: Impact = Impact.HIGH,
    kind: EventKind = EventKind.RELEASE,
    source: str = "faireconomy",
    currency: str = "USD",
    url: str | None = None,
    summary: str = "",
) -> EconomicEvent:
    return EconomicEvent(
        key=event_key(source, currency, title, at),
        starts_at=at,
        currency=currency,
        title=title,
        impact=impact,
        kind=kind,
        country="US",
        forecast="0.3%" if kind is EventKind.RELEASE else None,
        previous="0.2%" if kind is EventKind.RELEASE else None,
        source=source,
        url=url,
        summary=summary,
    )


def store_events(engine: Engine, *events: EconomicEvent, analyse: bool = True) -> NewsStore:
    store = NewsStore(engine)
    store.upsert_events(events, NOW)
    if analyse:
        for one in events:
            store.save_analysis(fallback_analysis(one, STATES, NOW))
    return store


def report(
    source: str = "faireconomy",
    *,
    ok: bool = True,
    at: datetime = NOW - timedelta(minutes=10),
    **extra: object,
) -> SourceReport:
    return SourceReport(source, ok, 3 if ok else 0, at, status=200 if ok else 500, **extra)  # type: ignore[arg-type]


def page(client: TestClient, url: str = "/calendar") -> str:
    """The page as the reader sees it: entities decoded, so apostrophes compare as typed."""
    return unescape(client.get(url).text)


@pytest.fixture
def client(engine: Engine) -> TestClient:
    return TestClient(create_app(engine, now=lambda: NOW))


@pytest.fixture
def filled(engine: Engine) -> NewsStore:
    cpi = event("Core CPI m/m", CPI_AT)
    claims = event("Unemployment Claims", NOW + timedelta(days=2, hours=1), impact=Impact.MEDIUM)
    speech = event(
        "Fed Chair Powell speaks",
        NOW + timedelta(hours=4),
        kind=EventKind.SPEECH,
        source="fed",
        url="https://www.federalreserve.gov/newsevents/speech/powell.htm",
        summary="Remarks on the economic outlook.",
    )
    headline = event(
        "Gold slips as the dollar firms",
        NOW - timedelta(hours=5),
        kind=EventKind.HEADLINE,
        source="fxstreet",
        impact=Impact.MEDIUM,
        url="https://www.fxstreet.com/news/gold",
    )
    store = store_events(engine, cpi, claims, speech, headline)
    store.record_fetch(report())
    store.record_fetch(report("fed"))
    store.save_brief(fallback_brief("2026-10-07", [cpi, speech], {}, STATES, NOW))
    return store


# --- the page -------------------------------------------------------------------------


def test_the_page_renders_on_an_empty_database_and_says_nothing_was_fetched(
    client: TestClient,
) -> None:
    response = client.get("/calendar")
    assert response.status_code == 200
    html = unescape(response.text)
    assert "Annonces économiques" in html
    assert "n'a jamais été rafraîchi" in html  # the state is told, not hidden
    assert "Aucune source n'a encore été interrogée" in html
    assert "Aucune annonce enregistrée aujourd'hui" in html
    assert "pas un conseil en investissement" in html


def test_the_page_shows_the_seeded_events_with_utc_and_paris_times(
    client: TestClient, filled: NewsStore
) -> None:
    html = page(client, "/calendar")
    assert "Core CPI m/m" in html
    assert "12:30" in html and "14:30 Paris" in html  # October 7th is summer time
    assert "Calendrier à jour." in html
    assert "XAUUSD" in html and "BTCUSD" in html
    assert "Prévision" in html and "Précédent" in html
    assert "Fed Chair Powell speaks" in html  # speeches section
    assert "Gold slips as the dollar firms" in html  # headlines section
    assert 'data-reading="live"' not in html  # every shown event has a stored analysis


def test_an_event_with_no_stored_analysis_is_read_by_the_rules_and_labelled(
    engine: Engine, client: TestClient
) -> None:
    store_events(engine, event("Core CPI m/m", CPI_AT), analyse=False)
    html = page(client, "/calendar")
    assert "Règles, calculées à l'affichage" in html
    assert 'data-reading="live"' in html


def test_the_actual_figure_is_never_invented(client: TestClient, filled: NewsStore) -> None:
    html = page(client, "/calendar")
    assert "<dt>Réel</dt>" not in html  # no actual from the source: the cell is hidden
    assert 'class="has-actual"' not in html


def test_a_day_link_selects_that_day_without_javascript(
    client: TestClient, filled: NewsStore
) -> None:
    day = (NOW + timedelta(days=2)).date().isoformat()
    html = page(client, f"/calendar?day={day}")
    panel = html.split('id="day-panel"', 1)[1].split("</section>", 1)[0]
    assert "Unemployment Claims" in panel
    assert "Core CPI m/m" not in panel
    assert f'href="/calendar?day={day}' in html


def test_a_day_without_announcements_says_so_and_why_it_may_be_empty(
    client: TestClient, filled: NewsStore
) -> None:
    html = page(client, "/calendar?day=2026-10-20")
    panel = html.split('id="day-panel"', 1)[1].split("</section>", 1)[0]
    assert "0 annonce" in panel
    assert "Core CPI" not in panel


def test_a_month_can_be_browsed(client: TestClient, filled: NewsStore) -> None:
    html = page(client, "/calendar?month=2026-11")
    assert "Novembre 2026" in html
    assert "/calendar?month=2026-10" in html and "/calendar?month=2026-12" in html


@pytest.mark.parametrize(
    "query",
    [
        "day=not-a-date",
        "day=2026-02-30",
        "day=2026-13-01",
        "month=2026-13",
        "month=garbage",
        "day=%3Cscript%3E",
    ],
)
def test_a_malformed_date_is_a_400_not_a_crash(client: TestClient, query: str) -> None:
    response = client.get(f"/calendar?{query}")
    assert response.status_code == 400
    assert "Date invalide" in unescape(response.text)
    assert "day=<script>" not in response.text


def test_feed_text_is_escaped_and_a_javascript_link_is_never_rendered(
    engine: Engine, client: TestClient
) -> None:
    hostile = event(
        "<script>alert(1)</script> speech",
        NOW + timedelta(hours=2),
        kind=EventKind.SPEECH,
        source="fed",
        url="javascript:alert(1)",
        summary="<img src=x onerror=alert(1)>",
    )
    store_events(engine, hostile)
    html = client.get("/calendar").text  # raw: the escaping itself is under test
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;" in html
    assert 'href="javascript:' not in html
    assert "<img src=x" not in html


# --- degraded states ------------------------------------------------------------------


def test_a_stale_calendar_is_flagged_as_stale(engine: Engine, client: TestClient) -> None:
    store = store_events(engine, event("Core CPI m/m", CPI_AT))
    store.record_fetch(report(at=NOW - timedelta(hours=9)))
    html = page(client, "/calendar")
    assert "périmé" in html
    assert 'class="banner bad"' in html


def test_a_failing_secondary_source_degrades_the_banner_and_names_the_error(
    engine: Engine, client: TestClient
) -> None:
    store = store_events(engine, event("Core CPI m/m", CPI_AT))
    store.record_fetch(report())
    store.record_fetch(report("fed", ok=False))
    html = page(client, "/calendar")
    assert 'class="banner warn"' in html
    assert "indisponible (HTTP 500)" in html


def test_a_rate_limited_source_is_told_with_its_wait(engine: Engine, client: TestClient) -> None:
    store = store_events(engine, event("Core CPI m/m", CPI_AT))
    store.record_fetch(report())
    store.record_fetch(
        SourceReport(
            "faireconomy_next", False, 0, NOW, status=429, error="HTTP 429", retry_after=290
        )
    )
    assert "réessai dans" in page(client, "/calendar")


def test_a_fresh_optional_week_does_not_hide_a_stale_primary_calendar(
    engine: Engine, client: TestClient
) -> None:
    store = store_events(engine, event("Core CPI m/m", CPI_AT))
    store.record_fetch(report(at=NOW - timedelta(hours=9)))
    store.record_fetch(report("faireconomy_next", at=NOW - timedelta(minutes=5)))
    html = page(client, "/calendar")
    assert "périmé" in html
    assert 'class="banner bad"' in html


def test_an_empty_weekday_calendar_is_a_red_banner_not_a_quiet_week(
    engine: Engine, client: TestClient
) -> None:
    store = store_events(engine, event("Core CPI m/m", CPI_AT))
    store.record_fetch(report(at=NOW - timedelta(hours=2)))
    store.record_fetch(
        SourceReport(
            "faireconomy",
            False,
            0,
            NOW - timedelta(minutes=5),
            status=200,
            error=f"{EMPTY_CALENDAR} : 200 sans annonce pertinente",
        )
    )
    html = page(client, "/calendar")
    assert 'class="banner bad"' in html
    assert "Calendrier vide" in html
    assert "Calendrier à jour." not in html


def test_a_stored_url_that_cannot_be_parsed_never_breaks_the_page(
    engine: Engine, client: TestClient
) -> None:
    speech = event(
        "Fed Chair Powell speaks",
        NOW + timedelta(hours=2),
        kind=EventKind.SPEECH,
        source="fed",
        url="https://www.federalreserve.gov/x",
    )
    store_events(engine, speech)
    with engine.begin() as connection:  # what an older version could have stored
        connection.execute(text("UPDATE economic_events SET url = 'http://[::1/x'"))
    response = client.get("/calendar")
    assert response.status_code == 200
    assert "Fed Chair Powell speaks" in unescape(response.text)
    assert "[::1" not in response.text


@pytest.mark.parametrize("query", ["day=9999-12-31", "day=0001-01-01", "day=1899-12-31"])
def test_an_out_of_range_year_is_a_400_like_a_malformed_date(
    client: TestClient, query: str
) -> None:
    response = client.get(f"/calendar?{query}")
    assert response.status_code == 400
    assert "Date invalide" in unescape(response.text)


def test_parse_day_bounds_the_year_like_parse_month() -> None:
    with pytest.raises(BadCalendarRequest):
        parse_day("9999-12-31")
    assert parse_day("2200-12-31") is not None


# --- shell integration ----------------------------------------------------------------


def test_the_navigation_lists_the_calendar_and_marks_it_current(client: TestClient) -> None:
    html = page(client, "/calendar")
    assert 'href="/calendar"' in html
    assert "Calendrier" in html
    assert 'aria-current="page"' in html
    assert "/static/css/calendar.css" in html and "/static/js/calendar.js" in html
    other = page(client, "/")
    assert 'href="/calendar"' in other
    assert "/static/css/calendar.css" not in other  # the sheet loads on its own page only


def test_the_calendar_assets_are_served(client: TestClient) -> None:
    assert client.get("/static/css/calendar.css").status_code == 200
    script = client.get("/static/js/calendar.js")
    assert script.status_code == 200
    assert "fetch(" in script.text


def test_the_page_is_read_only(client: TestClient) -> None:
    assert client.post("/calendar").status_code in (404, 405)
    assert client.delete("/calendar").status_code in (404, 405)


def test_news_writes_move_the_live_fingerprint(engine: Engine) -> None:
    before = fingerprint(engine)
    store = store_events(engine, event("Core CPI m/m", CPI_AT))
    after_events = fingerprint(engine)
    assert after_events != before
    store.record_fetch(report())
    after_fetch = fingerprint(engine)
    assert after_fetch != after_events
    store.save_brief(fallback_brief("2026-10-07", [], {}, STATES, NOW))
    assert fingerprint(engine) != after_fetch


# --- helpers --------------------------------------------------------------------------


def test_paris_is_two_hours_ahead_in_summer_and_one_in_winter() -> None:
    assert paris_offset(datetime(2026, 7, 1, 12, tzinfo=UTC)) == timedelta(hours=2)
    assert paris_offset(datetime(2026, 1, 15, 12, tzinfo=UTC)) == timedelta(hours=1)
    assert to_paris(datetime(2026, 10, 7, 12, 30, tzinfo=UTC)).hour == 14
    assert to_paris(datetime(2026, 12, 7, 12, 30, tzinfo=UTC)).hour == 13


def test_paris_summer_time_switches_at_01_utc_on_the_last_sunday() -> None:
    # 2026: March 29th and October 25th.
    assert paris_offset(datetime(2026, 3, 29, 0, 59, tzinfo=UTC)) == timedelta(hours=1)
    assert paris_offset(datetime(2026, 3, 29, 1, 0, tzinfo=UTC)) == timedelta(hours=2)
    assert paris_offset(datetime(2026, 10, 25, 0, 59, tzinfo=UTC)) == timedelta(hours=2)
    assert paris_offset(datetime(2026, 10, 25, 1, 0, tzinfo=UTC)) == timedelta(hours=1)


def test_the_query_parsers_accept_only_real_dates() -> None:
    assert parse_day("2026-10-07") is not None
    assert parse_day(None) is None
    assert parse_month("2026-10") == (2026, 10)
    assert parse_month(None) is None
