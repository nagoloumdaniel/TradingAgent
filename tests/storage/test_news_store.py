from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Engine, inspect

from tradingagent.core.mode import AiFilter
from tradingagent.news.model import (
    BTCUSD,
    XAUUSD,
    Bias,
    DailyBrief,
    EconomicEvent,
    EventAnalysis,
    EventKind,
    Impact,
    MarketBrief,
    MarketVerdict,
    Stance,
    event_key,
)
from tradingagent.news.ports import SourceReport
from tradingagent.storage.ai_calls import AiCall, AiCallStore
from tradingagent.storage.news import NewsStore

NOW = datetime(2026, 10, 30, 6, 0, tzinfo=UTC)
AT = datetime(2026, 10, 30, 12, 30, tzinfo=UTC)


def event(
    title: str = "CPI m/m",
    *,
    at: datetime = AT,
    source: str = "faireconomy",
    forecast: str | None = "0.3%",
    actual: str | None = None,
) -> EconomicEvent:
    return EconomicEvent(
        key=event_key(source, "USD", title, at),
        starts_at=at,
        currency="USD",
        title=title,
        impact=Impact.HIGH,
        kind=EventKind.RELEASE,
        country="USD",
        forecast=forecast,
        previous="0.2%",
        actual=actual,
        source=source,
    )


def analysis(key: str, source: str = "ai", at: datetime = NOW) -> EventAnalysis:
    verdicts = tuple(
        MarketVerdict(
            symbol=symbol,
            bias=Bias.BEARISH,
            confidence=0.6,
            influence=70,
            stance=Stance.WAIT,
            timeframes=("M15", "H1"),
            window_start=AT,
            window_end=AT + timedelta(hours=2),
            rationale="Inflation plus forte : dollar soutenu.",
        )
        for symbol in (XAUUSD, BTCUSD)
    )
    return EventAnalysis(key, verdicts, source, "deepseek-flash", at, "abc123")


def test_the_migration_created_the_news_tables(engine: Engine) -> None:
    names = set(inspect(engine).get_table_names())
    assert {"economic_events", "news_analyses", "news_briefs", "news_fetches"} <= names


def test_an_event_round_trips_with_utc_datetimes(engine: Engine) -> None:
    store = NewsStore(engine)
    assert store.upsert_events([event()], NOW) == 1
    [stored] = store.events_between(AT - timedelta(hours=1), AT + timedelta(hours=1))
    assert stored == event()
    assert stored.starts_at.utcoffset() == timedelta(0)


def test_refreshing_the_same_event_is_not_a_second_row(engine: Engine) -> None:
    store = NewsStore(engine)
    store.upsert_events([event()], NOW)
    assert store.upsert_events([event()], NOW + timedelta(minutes=20)) == 0
    assert store.count_events() == 1


def test_new_figures_update_the_row(engine: Engine) -> None:
    store = NewsStore(engine)
    store.upsert_events([event()], NOW)
    printed = event(actual="0.5%")
    assert store.upsert_events([printed], NOW + timedelta(hours=7)) == 1
    [stored] = store.events_between(AT - timedelta(hours=1), AT + timedelta(hours=1))
    assert stored.actual == "0.5%"


def test_events_are_read_in_time_order_inside_a_half_open_range(engine: Engine) -> None:
    store = NewsStore(engine)
    later = event("Retail Sales m/m", at=AT + timedelta(hours=3))
    store.upsert_events([later, event()], NOW)
    assert [e.title for e in store.events_between(AT, AT + timedelta(days=1))] == [
        "CPI m/m",
        "Retail Sales m/m",
    ]
    assert store.events_between(AT, AT + timedelta(hours=3)) != []
    assert [e.title for e in store.events_between(AT, AT + timedelta(hours=3))] == ["CPI m/m"]


def test_an_event_a_source_stopped_listing_is_withdrawn_inside_its_span(engine: Engine) -> None:
    store = NewsStore(engine)
    moved = event("CPI m/m", at=AT + timedelta(hours=1))
    store.upsert_events([event(), moved, event("Other", source="rss")], NOW)
    # The source now lists only the rescheduled one.
    removed = store.withdraw(
        "faireconomy", AT - timedelta(days=1), AT + timedelta(days=1), [moved.key]
    )
    assert removed == 1
    titles = {
        (e.title, e.source)
        for e in store.events_between(AT - timedelta(days=1), AT + timedelta(days=1))
    }
    assert titles == {("CPI m/m", "faireconomy"), ("Other", "rss")}


def test_the_latest_analysis_wins(engine: Engine) -> None:
    store = NewsStore(engine)
    key = event().key
    store.save_analysis(analysis(key, "fallback"))
    store.save_analysis(analysis(key, "ai", NOW + timedelta(hours=1)))
    latest = store.latest_analyses([key, "missing"])
    assert set(latest) == {key}
    assert latest[key].source == "ai"
    assert latest[key].verdicts[0].window_start == AT
    assert latest[key].verdicts[0].timeframes == ("M15", "H1")
    assert store.latest_analyses([]) == {}


def test_the_brief_round_trips_and_the_day_is_filterable(engine: Engine) -> None:
    store = NewsStore(engine)
    brief = DailyBrief(
        day="2026-10-30",
        headline="Journée dominée par le CPI.",
        markets=(
            MarketBrief(XAUUSD, Bias.BEARISH, Stance.WAIT, "Attendre le CPI."),
            MarketBrief(BTCUSD, Bias.NEUTRAL, Stance.WAIT, "Pas de catalyseur propre."),
        ),
        source="ai",
        model="deepseek-flash",
        generated_at=NOW,
    )
    store.save_brief(brief)
    assert store.latest_brief("2026-10-30") == brief
    assert store.latest_brief() == brief
    assert store.latest_brief("2026-10-29") is None


def test_the_last_fetch_of_each_source_is_what_comes_back(engine: Engine) -> None:
    store = NewsStore(engine)
    store.record_fetch(SourceReport("faireconomy", True, 90, NOW, status=200))
    store.record_fetch(
        SourceReport(
            "faireconomy", False, 0, NOW + timedelta(minutes=20), 429, "limite", retry_after=290
        )
    )
    store.record_fetch(SourceReport("coindesk", True, 4, NOW, status=200, note="ok"))
    reports = {r.source: r for r in store.latest_fetches()}
    assert set(reports) == {"faireconomy", "coindesk"}
    assert reports["faireconomy"].ok is False
    assert reports["faireconomy"].retry_after == 290
    assert reports["coindesk"].note == "ok"
    assert store.last_success("faireconomy") == NOW
    assert store.last_success("nobody") is None


def test_the_news_cost_is_read_back_from_the_ai_journal(engine: Engine) -> None:
    calls = AiCallStore(engine)
    store = NewsStore(engine)

    def record(purpose: str, cost: str, at: datetime) -> None:
        calls.record(
            AiCall(
                signal_id=None,
                purpose=purpose,
                ai_filter=AiFilter.SHADOW,
                request={},
                response=None,
                verdict=None,
                error=None,
                latency_ms=10,
                cost_eur=Decimal(cost),
                called_at=at,
            )
        )

    record("news_events", "0.0100", NOW)
    record("news_brief", "0.0050", NOW + timedelta(hours=1))
    record("signal_review", "9.0000", NOW)  # not news
    record("news_events", "0.5000", NOW + timedelta(days=1))  # another day
    day_end = NOW + timedelta(hours=12)
    assert store.ai_cost_between(NOW - timedelta(hours=1), day_end) == Decimal("0.0150")
    assert store.ai_calls_between(NOW - timedelta(hours=1), day_end) == 2


def test_purge_forgets_the_old_but_keeps_the_recent(engine: Engine) -> None:
    store = NewsStore(engine)
    old = event("Old", at=AT - timedelta(days=30))
    store.upsert_events([old, event()], NOW)
    store.save_analysis(analysis(old.key, at=AT - timedelta(days=30)))
    assert store.purge_before(AT - timedelta(days=14)) == 1
    assert store.count_events() == 1
    assert store.latest_analyses([old.key]) == {}


# --- ingestion hygiene: one hostile or oversized event never fails the batch ----------------


def test_an_event_with_an_invalid_currency_is_skipped_and_the_rest_stored(engine: Engine) -> None:
    store = NewsStore(engine)
    good = event("Good")
    bad = EconomicEvent(
        key=event_key("faireconomy", "US D", "Bad", AT),
        starts_at=AT,
        currency="US D",
        title="Bad",
        impact=Impact.HIGH,
        source="faireconomy",
    )
    lowercase = EconomicEvent(
        key=event_key("faireconomy", "usd", "Lower", AT),
        starts_at=AT,
        currency="usd",
        title="Lower",
        impact=Impact.HIGH,
        source="faireconomy",
    )
    assert store.upsert_events([bad, good, lowercase], NOW) == 2
    assert {
        e.title for e in store.events_between(AT - timedelta(hours=1), AT + timedelta(hours=1))
    } == {
        "Good",
        "Lower",
    }


def test_oversized_fields_are_cut_instead_of_failing_the_batch(engine: Engine) -> None:
    store = NewsStore(engine)
    huge = EconomicEvent(
        key=event_key("faireconomy", "USD", "Huge", AT),
        starts_at=AT,
        currency="USD",
        title="H" * 1000,
        impact=Impact.HIGH,
        country="UNITED STATES OF AMERICA",
        forecast="9" * 500,
        previous="8" * 500,
        actual="7" * 500,
        source="faireconomy",
        url="https://example.com/" + "u" * 800,
    )
    assert store.upsert_events([huge, event("Fine")], NOW) == 2
    stored = {
        e.title[:4]: e
        for e in store.events_between(AT - timedelta(hours=1), AT + timedelta(hours=1))
    }
    one = stored["HHHH"]
    assert len(one.title) == 240 and len(one.forecast or "") == 32 and len(one.country) <= 8
    assert len(one.previous or "") == 32 and len(one.actual or "") == 32
    assert one.url is None  # too long to store whole: dropped, never truncated into another URL


def test_a_non_https_url_is_never_stored(engine: Engine) -> None:
    store = NewsStore(engine)
    risky = event("Risky")
    risky = EconomicEvent(**{**risky.__dict__, "url": "javascript:alert(1)"})
    store.upsert_events([risky], NOW)
    [stored] = store.events_between(AT - timedelta(hours=1), AT + timedelta(hours=1))
    assert stored.url is None
