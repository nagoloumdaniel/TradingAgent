import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Engine

from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe
from tradingagent.news.analyst import NewsAnalyst
from tradingagent.news.config import BlackoutConfig, NewsConfig
from tradingagent.news.market_state import MarketStateReader
from tradingagent.news.model import (
    BTCUSD,
    XAUUSD,
    EconomicEvent,
    EventKind,
    Impact,
    event_key,
)
from tradingagent.news.ports import EMPTY_CALENDAR, FetchResult, SourceReport
from tradingagent.news.service import (
    KIND_REFRESH_FAILED,
    KIND_SOURCE_FAILED,
    NewsService,
)
from tradingagent.storage.ai_calls import AiCall, AiCallStore
from tradingagent.storage.events import SystemEventStore
from tradingagent.storage.news import NewsStore

NOW = datetime(2026, 10, 30, 7, 0, tzinfo=UTC)
CPI_AT = datetime(2026, 10, 30, 12, 30, tzinfo=UTC)


def release(
    title: str = "Core CPI m/m", at: datetime = CPI_AT, forecast: str = "0.3%"
) -> EconomicEvent:
    return EconomicEvent(
        key=event_key("faireconomy", "USD", title, at),
        starts_at=at,
        currency="USD",
        title=title,
        impact=Impact.HIGH,
        kind=EventKind.RELEASE,
        forecast=forecast,
        previous="0.2%",
        source="faireconomy",
    )


class FakeSource:
    def __init__(self, name: str = "faireconomy") -> None:
        self.name = name
        self.events: tuple[EconomicEvent, ...] = ()
        self.report: SourceReport | None = None
        self.fetches = 0
        self.crash: Exception | None = None

    async def fetch(self, now: datetime) -> FetchResult:
        self.fetches += 1
        if self.crash is not None:
            raise self.crash
        report = self.report or SourceReport(self.name, True, len(self.events), now, status=200)
        span = (now - timedelta(days=1), now + timedelta(days=8))
        return FetchResult(self.events, report, covers=span if report.ok else None)


class NoCandles:
    def latest(
        self, symbol: str, timeframe: Timeframe, count: int, closed_by: datetime | None = None
    ) -> list[Candle]:
        return []


class Journal:
    def __init__(self, store: AiCallStore) -> None:
        self._store = store
        self.calls: list[AiCall] = []

    def record(self, call: AiCall) -> None:
        self.calls.append(call)
        self._store.record(call)


class Rig:
    def __init__(self, engine: Engine, config: NewsConfig | None = None) -> None:
        self.store = NewsStore(engine)
        self.events = SystemEventStore(engine)
        self.source = FakeSource()
        self.journal = Journal(AiCallStore(engine))
        analyst = NewsAnalyst(
            None,  # no key: the rules read everything
            self.journal,
            model="deepseek-flash",
            spent_today=lambda day: Decimal(0),
            config=config or NewsConfig(),
        )
        self.service = NewsService(
            self.store,
            [self.source],
            analyst,
            MarketStateReader(NoCandles()),
            self.events,
            config or NewsConfig(),
        )


@pytest.fixture
def rig(engine: Engine) -> Rig:
    return Rig(engine)


def tick(rig: Rig, at: datetime):
    return asyncio.run(rig.service.tick(at))


def test_a_refresh_stores_events_and_reads_each_by_the_rules(rig: Rig) -> None:
    rig.source.events = (release(), release("Unemployment Claims", CPI_AT + timedelta(hours=1)))
    report = tick(rig, NOW)
    assert report is not None and report.changed == 2 and report.analysed == 2
    assert rig.store.count_events() == 2
    readings = rig.store.latest_analyses([e.key for e in rig.source.events])
    assert {a.source for a in readings.values()} == {"fallback"}
    assert rig.journal.calls == []  # no key, no call


def test_the_tick_is_throttled_by_the_poll_interval(rig: Rig) -> None:
    rig.source.events = (release(),)
    assert tick(rig, NOW) is not None
    assert tick(rig, NOW + timedelta(minutes=5)) is None
    assert rig.source.fetches == 1
    assert tick(rig, NOW + timedelta(minutes=21)) is not None
    assert rig.source.fetches == 2


def test_an_unchanged_event_is_not_read_again(rig: Rig) -> None:
    rig.source.events = (release(),)
    tick(rig, NOW)
    [first] = rig.store.latest_analyses([rig.source.events[0].key]).values()
    report = tick(rig, NOW + timedelta(minutes=21))
    assert report is not None and report.analysed == 0
    [second] = rig.store.latest_analyses([rig.source.events[0].key]).values()
    assert second.analysed_at == first.analysed_at


def test_a_changed_forecast_is_read_again(rig: Rig) -> None:
    rig.source.events = (release(),)
    tick(rig, NOW)
    rig.source.events = (release(forecast="0.5%"),)
    report = tick(rig, NOW + timedelta(minutes=21))
    assert report is not None and report.changed == 1 and report.analysed == 1


def test_a_rescheduled_event_leaves_no_ghost(rig: Rig) -> None:
    rig.source.events = (release(),)
    tick(rig, NOW)
    moved = release(at=CPI_AT + timedelta(hours=2))
    rig.source.events = (moved,)
    tick(rig, NOW + timedelta(minutes=21))
    stored = rig.store.events_between(NOW, NOW + timedelta(days=1))
    assert [e.key for e in stored] == [moved.key]


def test_a_dead_source_is_reported_once_an_hour_and_keeps_old_events(rig: Rig) -> None:
    rig.source.events = (release(),)
    tick(rig, NOW)
    rig.source.report = SourceReport("faireconomy", False, 0, NOW, status=500, error="HTTP 500")
    tick(rig, NOW + timedelta(minutes=21))
    tick(rig, NOW + timedelta(minutes=42))
    told = rig.events.latest(KIND_SOURCE_FAILED)
    assert told is not None and told.detail["source"] == "faireconomy"
    assert rig.store.count_events() == 1
    [feed] = rig.store.latest_fetches()
    assert feed.ok is False


def test_a_crashing_source_never_reaches_the_loop(rig: Rig) -> None:
    rig.source.crash = RuntimeError("boom")
    report = tick(rig, NOW)
    assert report is not None and report.reports[0].ok is False
    assert "boom" in (report.reports[0].error or "")


def test_a_rate_limited_source_is_left_alone_for_as_long_as_asked(rig: Rig) -> None:
    rig.source.report = SourceReport(
        "faireconomy", False, 0, NOW, status=429, error="HTTP 429", retry_after=900
    )
    tick(rig, NOW)
    assert rig.source.fetches == 1
    tick(rig, NOW + timedelta(minutes=21))  # 21 min > 15 min: the wait is over
    assert rig.source.fetches == 2
    rig.source.report = SourceReport(
        "faireconomy", False, 0, NOW, status=429, error="HTTP 429", retry_after=7200
    )
    tick(rig, NOW + timedelta(minutes=42))
    assert rig.source.fetches == 3
    report = tick(rig, NOW + timedelta(minutes=63))
    assert rig.source.fetches == 3
    assert report is not None and any("limite de débit" in n for n in report.notes)


def test_the_morning_brief_is_written_once_per_day(rig: Rig) -> None:
    rig.source.events = (release(),)
    first = tick(rig, NOW)  # 07:00 UTC, after the 06:00 daily hour
    assert first is not None and first.brief_written
    brief = rig.store.latest_brief("2026-10-30")
    assert brief is not None and brief.source == "fallback"
    again = tick(rig, NOW + timedelta(minutes=21))
    assert again is not None and not again.brief_written


def test_no_brief_before_the_daily_hour(rig: Rig) -> None:
    rig.source.events = (release(),)
    early = tick(rig, datetime(2026, 10, 30, 3, 0, tzinfo=UTC))
    assert early is not None and not early.brief_written
    assert rig.store.latest_brief("2026-10-30") is None


def test_the_gate_blocks_inside_the_window_of_a_fresh_calendar(rig: Rig) -> None:
    rig.source.events = (release(),)
    tick(rig, NOW)
    near = CPI_AT - timedelta(minutes=10)
    decision = rig.service.check(XAUUSD, near)
    assert decision.blocked and decision.event is not None
    assert "Core CPI" in decision.reason
    assert not rig.service.check(XAUUSD, CPI_AT - timedelta(hours=2)).blocked


def test_the_gate_fails_open_and_says_so_when_the_calendar_is_stale(rig: Rig) -> None:
    rig.source.events = (release(),)
    tick(rig, NOW)
    late = NOW + timedelta(hours=8)  # past the 360-minute staleness limit
    decision = rig.service.check(XAUUSD, late)
    assert not decision.blocked and decision.degraded is not None
    assert "calendrier" in decision.degraded


def test_the_gate_before_any_refresh_is_open_and_degraded(rig: Rig) -> None:
    decision = rig.service.check(BTCUSD, NOW)
    assert not decision.blocked and decision.degraded == "calendrier jamais rafraîchi"


def test_a_disabled_gate_never_blocks(engine: Engine) -> None:
    config = NewsConfig(blackout=BlackoutConfig(enabled=False))
    off = Rig(engine, config)
    off.source.events = (release(),)
    asyncio.run(off.service.tick(NOW))
    assert not off.service.check(XAUUSD, CPI_AT).blocked


def test_a_disabled_news_service_does_nothing(engine: Engine) -> None:
    off = Rig(engine, NewsConfig(enabled=False))
    assert asyncio.run(off.service.tick(NOW)) is None
    assert off.source.fetches == 0
    assert off.service.upcoming_summary(NOW) == ""


def test_the_upcoming_summary_lists_what_is_about_to_land(rig: Rig) -> None:
    rig.source.events = (release(), release("Minor survey", CPI_AT + timedelta(days=3)))
    tick(rig, NOW)
    summary = rig.service.upcoming_summary(NOW)
    assert "Core CPI m/m" in summary and "12:30 UTC" in summary and "[majeure]" in summary
    assert "Minor survey" not in summary  # beyond the 24 h look-ahead
    assert "non rafraîchi" not in summary


def test_the_upcoming_summary_warns_when_the_calendar_is_stale(rig: Rig) -> None:
    rig.source.events = (release(),)
    tick(rig, NOW)
    assert "non rafraîchi" in rig.service.upcoming_summary(NOW + timedelta(hours=9))


def test_a_failing_refresh_is_recorded_not_raised(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(now: datetime):
        raise RuntimeError("store is gone")

    monkeypatch.setattr(rig.service, "refresh", broken)
    assert tick(rig, NOW) is None
    told = rig.events.latest(KIND_REFRESH_FAILED)
    assert told is not None and "store is gone" in str(told.detail["error"])


# --- freshness comes from the primary calendar only, and only after the write succeeded ----


def two_sources(engine: Engine, **options) -> tuple[Rig, FakeSource]:
    rig = Rig(engine, options.pop("config", None))
    second = FakeSource("faireconomy_next")
    analyst = rig.service._analyst
    rig.service = NewsService(
        rig.store,
        [rig.source, second],
        analyst,
        MarketStateReader(NoCandles()),
        rig.events,
        rig.service.config,
        **options,
    )
    return rig, second


def test_a_fresh_optional_week_never_hides_a_stale_primary_calendar(engine: Engine) -> None:
    rig, second = two_sources(engine)
    rig.source.events = (release(),)
    tick(rig, NOW)
    # The primary now fails for hours while the optional next-week feed keeps answering 200.
    rig.source.report = SourceReport("faireconomy", False, 0, NOW, status=500, error="HTTP 500")
    for step in range(1, 25):
        tick(rig, NOW + timedelta(minutes=21 * step))
    late = NOW + timedelta(minutes=21 * 24)
    next_week = rig.store.last_success("faireconomy_next")
    primary = rig.store.last_success("faireconomy")
    assert next_week is not None
    assert primary is not None
    assert next_week > primary
    assert rig.service.staleness(late) == late - primary
    decision = rig.service.check(XAUUSD, late)
    assert not decision.blocked and "calendrier rafraîchi il y a" in decision.degraded
    assert second.fetches >= 1


def test_a_failed_write_is_not_logged_as_a_successful_refresh(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.source.events = (release(),)

    def broken(*args: object, **kwargs: object) -> int:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(rig.store, "upsert_events", broken)
    report = tick(rig, NOW)
    assert report is not None  # the loop is never stopped
    assert rig.store.last_success("faireconomy") is None
    [feed] = rig.store.latest_fetches()
    assert feed.ok is False and "RuntimeError" in (feed.error or "")
    assert rig.service.check(XAUUSD, NOW).degraded == "calendrier jamais rafraîchi"
    assert rig.events.latest(KIND_SOURCE_FAILED) is not None


def test_a_weekday_200_without_any_event_makes_the_gate_degraded(rig: Rig) -> None:
    rig.source.events = (release(),)
    tick(rig, NOW)
    assert rig.service.check(XAUUSD, NOW).degraded == ""
    empty = SourceReport(
        "faireconomy", False, 0, NOW, status=200, error=f"{EMPTY_CALENDAR} : 200 sans annonce"
    )
    rig.source.report = empty
    tick(rig, NOW + timedelta(minutes=21))
    decision = rig.service.check(XAUUSD, NOW + timedelta(minutes=22))
    assert not decision.blocked
    assert EMPTY_CALENDAR in decision.degraded
    # The next good answer lifts it.
    rig.source.report = None
    tick(rig, NOW + timedelta(minutes=42))
    assert rig.service.check(XAUUSD, NOW + timedelta(minutes=43)).degraded == ""


def stale_rig(engine: Engine, *, fail_closed: bool, live: bool) -> Rig:
    rig = Rig(engine, NewsConfig(fail_closed_when_blind=fail_closed))
    rig.service = NewsService(
        rig.store,
        [rig.source],
        rig.service._analyst,
        MarketStateReader(NoCandles()),
        rig.events,
        rig.service.config,
        live=live,
    )
    return rig


def test_a_blind_calendar_refuses_in_live_when_fail_closed_is_on(engine: Engine) -> None:
    rig = stale_rig(engine, fail_closed=True, live=True)
    decision = rig.service.check(XAUUSD, NOW)  # never refreshed
    assert decision.blocked
    assert "fail_closed_when_blind" in decision.reason
    assert decision.degraded == "calendrier jamais rafraîchi"
    assert decision.event is None


def test_a_blind_calendar_never_refuses_outside_live_or_without_the_option(engine: Engine) -> None:
    for fail_closed, live in ((True, False), (False, True), (False, False)):
        rig = stale_rig(engine, fail_closed=fail_closed, live=live)
        decision = rig.service.check(XAUUSD, NOW)
        assert not decision.blocked and decision.degraded, (fail_closed, live)


def test_the_option_is_off_by_default() -> None:
    assert NewsConfig().fail_closed_when_blind is False


def test_a_hanging_source_cannot_stall_the_refresh(rig: Rig) -> None:
    class Hanging:
        name = "faireconomy"

        async def fetch(self, now: datetime) -> FetchResult:
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

    rig.service._sources = (Hanging(),)
    rig.service._source_deadline = 0.05
    report = tick(rig, NOW)
    assert report is not None and report.reports[0].ok is False
    assert "délai" in (report.reports[0].error or "")


def test_a_source_error_keeps_its_type_and_a_short_message(rig: Rig) -> None:
    rig.source.crash = RuntimeError("x" * 5000)
    report = tick(rig, NOW)
    assert report is not None
    error = report.reports[0].error or ""
    assert error.startswith("RuntimeError") and len(error) < 400


def test_a_long_feed_title_is_cut_in_the_model_context(rig: Rig) -> None:
    rig.source.events = (release("Z" * 600, CPI_AT),)
    tick(rig, NOW)
    summary = rig.service.upcoming_summary(NOW, hours=24)
    line = next(part for part in summary.splitlines() if "ZZZZ" in part)
    assert "Z" * 140 in line and "Z" * 141 not in line
