"""The agent loop: one cycle, no terminal, everything injected."""

import asyncio
import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from tests.runtime.fakes import (
    BTC,
    LOGIN,
    NOW,
    RISK,
    FakeBroker,
    FakeNotifier,
    candle,
    open_calendar,
    weekend_calendar,
)

from tradingagent.config.agent import load_agent_config
from tradingagent.control.guardian import Guardian
from tradingagent.core.halt import CONNECTION, GLOBAL, session_scope
from tradingagent.core.market import Candle
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import Severity
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.history import HistorySync
from tradingagent.data.market_calendar import MarketCalendar
from tradingagent.data.market_data import (
    ClockMismatchError,
    ClockUnverifiableError,
    Subscription,
)
from tradingagent.notify.health_alerts import HealthAlerter
from tradingagent.notify.sensitive_commands import RESTART_EVENT
from tradingagent.reporting.service import ReportService
from tradingagent.risk.model import ClosedPosition
from tradingagent.runtime.loop import AgentLoop
from tradingagent.runtime.pipeline import SignalPipeline
from tradingagent.runtime.portfolio import PortfolioBuilder
from tradingagent.signals.generator import SignalGenerator
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.events import SystemEventStore
from tradingagent.storage.halts import HaltStore
from tradingagent.storage.models import SystemEventRow
from tradingagent.storage.signals import SignalRepository
from tradingagent.strategies.registry import REGISTRY

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_STRATEGIES = ROOT / "config" / "strategies"
SHIPPED_AGENT = ROOT / "config" / "agent.yaml"
BROKER_SYMBOLS = {"XAUUSD", "BTCUSD"}


class FakeMarket:
    def __init__(self, *, connected: bool = True, clock_ok: bool = True) -> None:
        self.connected = connected
        self.clock_ok = clock_ok
        self.queue: list[Candle] = []
        self.baselined = False
        self.selected: set[str] = set()

    async def select(self, symbols: Iterable[str]) -> set[str]:
        self.selected = set(symbols)
        return set(symbols)

    async def poll_new(self, subscriptions: Iterable[Subscription]) -> list[Candle]:
        del subscriptions
        if not self.baselined:
            self.baselined = True
            return []
        queued, self.queue = self.queue, []
        return list(queued)

    async def closed_candles(self, symbol: str, timeframe: Timeframe, count: int) -> list[Candle]:
        del symbol, timeframe, count
        return []

    async def last_tick_at(self, symbol: str) -> datetime | None:
        del symbol
        return NOW

    async def verify_clock(self) -> None:
        if not self.clock_ok:
            raise ClockMismatchError("server offset moved")

    async def ensure_connected(self, max_attempts: int | None = None) -> bool:
        del max_attempts
        return self.connected

    async def close(self) -> None:
        return None


class UnverifiableClockMarket(FakeMarket):
    """A probe that serves no tick: the 2026-10-07 failure, seen from the loop.

    The distinction the loop has to make is between a clock that *moved* — a measurement,
    and a stop — and a clock it *could not measure* — no measurement at all.
    """

    async def verify_clock(self) -> None:
        if not self.clock_ok:
            raise ClockUnverifiableError("no tick on BTCUSD to verify the server clock")


class MutableClock:
    """A clock a test can advance, so an episode can be made to last."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class CountingGenerator:
    """The strategy layer, reduced to the one question that matters here: was it asked?"""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def strategies(self) -> tuple[object, ...]:
        return ()

    def on_candle_closed(
        self,
        symbol: str,
        candle: Candle,
        calendar: MarketCalendar,
        now: datetime,
        last_tick_at: datetime | None,
    ) -> tuple[object, ...]:
        del symbol, candle, calendar, now, last_tick_at
        self.calls += 1
        return ()


class Sent:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, text: str) -> None:
        self.messages.append(text)


class ExplodingNotifier:
    """A Telegram that is down: a restart must not depend on it answering."""

    def __init__(self) -> None:
        self.attempts = 0

    async def send(self, text: str, *, parse_mode: str | None = None) -> bool:
        del text, parse_mode
        self.attempts += 1
        raise RuntimeError("telegram is down")


def build_loop(
    engine: Engine,
    *,
    market: FakeMarket | None = None,
    broker: FakeBroker | None = None,
    connection_threshold: timedelta = timedelta(0),
    ea_directory: Path | None = None,
    calendars: dict[str, MarketCalendar] | None = None,
    subscriptions: tuple[Subscription, ...] | None = None,
    generator: SignalGenerator | None = None,
    notifier: FakeNotifier | ExplodingNotifier | None = None,
    clock: Callable[[], datetime] | None = None,
    now: datetime = NOW,
) -> tuple[AgentLoop, FakeMarket, FakeBroker, Sent]:
    market = market or FakeMarket()
    broker = broker or FakeBroker()
    store = CandleStore(engine)
    halts = HaltStore(engine)
    events = SystemEventStore(engine)
    sent = Sent()
    alerts = HealthAlerter(sent, events, disk_path=ROOT)
    notifier = notifier if notifier is not None else FakeNotifier()
    learned = calendars if calendars is not None else {}
    moment = clock if clock is not None else lambda: now
    config = load_agent_config(
        SHIPPED_AGENT,
        known_symbols=BROKER_SYMBOLS,
        strategies={ref: loaded.manifest for ref, loaded in _catalog().items()},
        mode=TradingMode.SIGNAL,
    )
    pipeline = SignalPipeline(
        engine=engine,
        halts=halts,
        broker=broker,
        notifier=notifier,
        portfolio=PortfolioBuilder(engine),
        risk_config=RISK,
        expected_login=LOGIN,
        mode=TradingMode.SIGNAL,
        calendar_for=learned.get,
        now=moment,
    )
    loop = AgentLoop(
        engine=engine,
        market=market,
        store=store,
        history=HistorySync(market, store, now=moment),
        generator=(
            generator
            if generator is not None
            else SignalGenerator((), store, SignalRepository(engine), TradingMode.SIGNAL)
        ),
        pipeline=pipeline,
        broker=broker,
        halts=halts,
        notifier=notifier,
        guardian=Guardian(halts, now=moment),
        alerts=alerts,
        reports=ReportService(engine, sender=sent),
        portfolio=PortfolioBuilder(engine),
        config=config,
        mode=TradingMode.SIGNAL,
        expected_login=LOGIN,
        subscriptions=(
            subscriptions if subscriptions is not None else (Subscription("XAUUSD", Timeframe.M15),)
        ),
        calendars=learned,
        ea_directory=ea_directory,
        connection_threshold=connection_threshold,
        now=moment,
    )
    return loop, market, broker, sent


def _catalog():
    from tradingagent.config.strategy_catalog import load_strategy_catalog

    return load_strategy_catalog(SHIPPED_STRATEGIES, REGISTRY)


def events_of(engine: Engine, kind: str) -> list[SystemEventRow]:
    with Session(engine) as session:
        return list(
            session.scalars(select(SystemEventRow).where(SystemEventRow.kind == kind)).all()
        )


def test_a_published_candle_is_stored_and_counted(engine: Engine) -> None:
    loop, market, _, _ = build_loop(engine)
    asyncio.run(loop.run_once())  # the first poll only sets the baseline
    market.queue.append(candle(close=2400.0))

    report = asyncio.run(loop.run_once())

    assert report.publications == 1
    assert CandleStore(engine).last_open_time("XAUUSD", Timeframe.M15) == NOW


def test_a_closure_the_broker_made_reaches_the_operator_and_the_counter(
    engine: Engine,
) -> None:
    """F-017: a stop-out is an outcome. Collected only by reconciliation, it is a log line
    in the broker and nothing the operator was ever told."""
    broker = FakeBroker()
    notifier = FakeNotifier()
    loop, _, _, _ = build_loop(
        engine, broker=broker, notifier=notifier, calendars={"XAUUSD": open_calendar()}
    )
    asyncio.run(loop.run_once())  # baseline
    broker.queue_close(
        ClosedPosition(
            ticket=777001,
            symbol="XAUUSD",
            exit_price=Decimal("2390.00"),
            pnl_eur=Decimal("-10.20"),
            exit_reason="stop_loss",
            closed_at=NOW,
            signal_id=None,
        )
    )

    report = asyncio.run(loop.run_once())

    assert report.closed_positions == 1
    assert notifier.messages, "the closure never reached the operator"
    assert "XAUUSD" in notifier.messages[0]


def test_a_lost_connection_alerts_and_halts_once_the_threshold_is_reached(
    engine: Engine,
) -> None:
    loop, _, _, sent = build_loop(engine, market=FakeMarket(connected=False))
    halts = HaltStore(engine)

    report = asyncio.run(loop.run_once())

    assert report.connection_lost is True
    assert report.halted is True
    assert halts.is_halted(CONNECTION) is True
    assert any("Coupure" in message for message in sent.messages)


def test_a_clock_mismatch_stops_the_cycle_without_publishing(engine: Engine) -> None:
    loop, market, _, _ = build_loop(engine, market=FakeMarket(clock_ok=False))
    market.queue.append(candle())

    report = asyncio.run(loop.run_once())

    assert report.publications == 0
    assert len(events_of(engine, "clock_mismatch")) == 1


# --- /restart: the loop is what closes a demand the command cannot -------------


def test_a_restart_asked_during_this_run_stops_the_loop_cleanly(engine: Engine) -> None:
    """Telegram cannot kill the process serving it; the loop reads the request and stops."""
    clock = MutableClock(NOW)
    notifier = FakeNotifier()
    loop, market, _, _ = build_loop(engine, now=NOW, clock=clock, notifier=notifier)
    market.queue.append(candle())
    SystemEventStore(engine).record(
        RESTART_EVENT, Severity.INFO, {"actor": "telegram:111"}, NOW + timedelta(minutes=1)
    )
    clock.now = NOW + timedelta(minutes=2)

    report = asyncio.run(loop.run_once())

    assert loop.restart_requested is True
    assert loop._stop is True, "run_forever leaves on the next check"
    assert report.publications == 0, "the cycle stops before trading, not after"
    assert len(events_of(engine, "restart_honoured")) == 1
    assert any("Redémarrage" in message for message in notifier.messages)


def test_a_restart_asked_before_this_run_is_not_honoured(engine: Engine) -> None:
    """`system_events` keeps every demand ever made: yesterday's must not loop the agent."""
    SystemEventStore(engine).record(
        RESTART_EVENT, Severity.INFO, {"actor": "telegram:111"}, NOW - timedelta(days=1)
    )
    loop, market, _, _ = build_loop(engine, now=NOW)
    market.queue.append(candle())
    asyncio.run(loop.run_once())  # the baseline
    market.queue.append(candle())

    report = asyncio.run(loop.run_once())

    assert loop.restart_requested is False
    assert loop._stop is False
    assert report.publications > 0, "an old demand changes nothing about the cycle"


def test_the_loop_keeps_running_when_the_restart_notice_cannot_be_sent(
    engine: Engine,
) -> None:
    """A Telegram outage must not leave an agent that was asked to restart running."""
    clock = MutableClock(NOW)
    notifier = ExplodingNotifier()
    loop, _, _, _ = build_loop(engine, now=NOW, clock=clock, notifier=notifier)
    SystemEventStore(engine).record(
        RESTART_EVENT, Severity.INFO, {"actor": "telegram:111"}, NOW + timedelta(minutes=1)
    )
    clock.now = NOW + timedelta(minutes=2)

    asyncio.run(loop.run_once())

    assert loop.restart_requested is True
    assert notifier.attempts == 1


class RestartableMarket(FakeMarket):
    """A terminal the operator restarted: the handle is dead until something reconnects.

    This is the shape of the real failure of 2026-10-07. MetaTrader was restarted, the
    agent's `last_tick` returned None on the stale handle, and the clock check raised. With
    the check running *before* the reconnection, every later cycle failed the same way and
    the agent never recovered — it had to be restarted by hand.
    """

    def __init__(self) -> None:
        super().__init__()
        self.dead = True
        self.calls: list[str] = []

    async def ensure_connected(self, max_attempts: int | None = None) -> bool:
        self.calls.append("ensure_connected")
        del max_attempts
        if self.dead:
            self.dead = False  # the reconnect attempt succeeds, as it does in reality
        return True

    async def verify_clock(self) -> None:
        self.calls.append("verify_clock")
        if self.dead:
            raise ClockMismatchError("no tick on BTCUSD to verify the server clock")


def test_a_restarted_terminal_is_survivable(engine: Engine) -> None:
    """The connection is restored before the clock is checked, so the agent heals itself."""
    market = RestartableMarket()
    loop, _, _, _ = build_loop(engine, market=market)

    asyncio.run(loop.run_once())

    assert market.calls[:2] == ["ensure_connected", "verify_clock"]
    assert market.dead is False, "the reconnect never happened"
    assert events_of(engine, "clock_mismatch") == []


def test_a_second_cycle_after_a_restart_is_normal(engine: Engine) -> None:
    """Once healed, the loop does its ordinary work again rather than staying wedged."""
    market = RestartableMarket()
    loop, _, _, _ = build_loop(engine, market=market)

    asyncio.run(loop.run_once())  # heals
    report = asyncio.run(loop.run_once())  # and carries on

    assert report.connection_lost is False
    assert "ensure_connected" in market.calls


def test_an_unverifiable_clock_keeps_the_cycle_going_but_not_the_strategies(
    engine: Engine,
) -> None:
    """2026-10-07, from the loop's side: two hours of the cycle doing nothing at all.

    A probe with no tick is not a clock that moved. The cycle keeps collecting, the
    positions keep being followed, the alarms keep being armed — and the one thing that
    stops is the trading decision, which is what the operator's alert promises.
    """
    market = UnverifiableClockMarket()
    counting = CountingGenerator()
    loop, _, _, _ = build_loop(
        engine,
        market=market,
        generator=cast(Any, counting),
        calendars={"XAUUSD": open_calendar()},
        connection_threshold=timedelta(minutes=10),
    )
    asyncio.run(loop.run_once())  # baseline, clock verified
    market.clock_ok = False
    market.queue.append(candle())

    report = asyncio.run(loop.run_once())

    assert report.publications == 1, "the candle was not collected"
    assert CandleStore(engine).last_open_time("XAUUSD", Timeframe.M15) == NOW
    assert counting.calls == 0, "a strategy was asked to speak on a clock we cannot vouch for"
    assert events_of(engine, "clock_mismatch") == [], "an unreadable probe was called a mismatch"
    assert [row.severity for row in events_of(engine, "clock_unverified")] == [Severity.WARNING]


def test_an_episode_is_reported_twice_at_most_however_long_it_lasts(engine: Engine) -> None:
    """383 identical CRITICALs were a record of the loop repeating itself, not of a problem."""
    clock = MutableClock(NOW)
    market = UnverifiableClockMarket()
    market.clock_ok = False
    loop, _, _, sent = build_loop(
        engine,
        market=market,
        clock=clock,
        connection_threshold=timedelta(minutes=10),
    )

    for minute in range(100):
        clock.now = NOW + timedelta(minutes=minute)
        asyncio.run(loop.run_once())

    assert [row.severity for row in events_of(engine, "clock_unverified")] == [
        Severity.WARNING,
        Severity.CRITICAL,
    ]
    # One message when the episode opens, then a reminder after the alerter's own cooldown:
    # an hour and a half of the same outage reaches the operator a handful of times.
    clock_alerts = [message for message in sent.messages if "horloge" in message]
    assert 1 <= len(clock_alerts) <= 5


def test_a_clock_that_comes_back_resumes_the_strategies(engine: Engine) -> None:
    """Recovery is automatic and it is announced — a recovery nobody hears is not one."""
    market = UnverifiableClockMarket()
    counting = CountingGenerator()
    loop, _, _, sent = build_loop(
        engine,
        market=market,
        generator=cast(Any, counting),
        calendars={"XAUUSD": open_calendar()},
        connection_threshold=timedelta(minutes=10),
    )
    asyncio.run(loop.run_once())
    market.clock_ok = False
    market.queue.append(candle())
    asyncio.run(loop.run_once())  # suspended
    market.clock_ok = True
    market.queue.append(candle(close=2401.0))

    asyncio.run(loop.run_once())  # resumed, without a restart

    assert counting.calls == 1
    assert [row.severity for row in events_of(engine, "clock_verified")] == [Severity.INFO]
    assert any("rétablie" in message for message in sent.messages)


def test_a_divergence_halts_globally_and_alerts(engine: Engine) -> None:
    broker = FakeBroker()
    broker.divergences = ("local ticket 7 is missing at the broker",)
    loop, _, _, sent = build_loop(engine, broker=broker)

    report = asyncio.run(loop.run_once())

    assert report.divergences == 1
    assert HaltStore(engine).is_halted(GLOBAL) is True
    assert events_of(engine, "position_divergence")
    assert any("Coupure" in message for message in sent.messages)


def test_the_loop_keeps_going_after_a_failed_cycle(engine: Engine) -> None:
    loop, market, _, _ = build_loop(engine, market=FakeMarket(connected=False))
    asyncio.run(loop.run_once())
    market.connected = True
    report = asyncio.run(loop.run_once())
    assert report.connection_lost is False
    assert report.reconnected is True


def test_the_snapshot_is_written_periodically(engine: Engine) -> None:
    loop, _, _, _ = build_loop(engine)
    loop._cycles = loop._cycles + 14  # the next cycle hits the snapshot cadence
    asyncio.run(loop.run_once())
    snapshots = loop._snapshots.latest_before(NOW)
    assert snapshots is not None
    assert snapshots.equity == Decimal("5497.74")


def test_a_closed_position_notifies_the_result_and_the_balance(engine: Engine) -> None:
    loop, _, _, _ = build_loop(engine)
    asyncio.run(
        loop.notify_close(
            ClosedPosition(
                ticket=555001,
                symbol="XAUUSD",
                exit_price=Decimal("2424"),
                pnl_eur=Decimal("-10.00"),
                exit_reason="stop_loss",
                closed_at=NOW,
            )
        )
    )
    notifier = cast(FakeNotifier, loop._notifier)
    [message] = notifier.messages
    lines = message.splitlines()
    assert lines[0] == "❌ XAUUSD"
    assert lines[1] == ""
    assert lines[2].startswith("<pre>Résultat")
    assert "-10.00 €" in lines[2]
    assert lines[3].endswith("</pre>") and "5 497.74 €" in lines[3]
    # Aligned: with the <pre> tags taken out, the two figures start at the same column.
    body = [line.removeprefix("<pre>").removesuffix("</pre>") for line in lines[2:4]]
    assert body[0].index("-10.00 €") == body[1].index("5 497.74 €")


def test_a_close_lands_in_the_daily_bucket_and_the_telemetry(engine: Engine) -> None:
    from tests.runtime.test_pipeline import record_signal

    from tradingagent.core.states import ExecutionEventKind
    from tradingagent.storage.daily import DailyPerformanceStore
    from tradingagent.storage.telemetry import ExecutionEventStore

    loop, _, _, _ = build_loop(engine)
    signal_id = record_signal(engine)
    loop._record_close(
        ClosedPosition(
            ticket=555002,
            symbol="XAUUSD",
            exit_price=Decimal("2424"),
            pnl_eur=Decimal("47.60"),
            exit_reason="take_profit",
            closed_at=NOW,
            signal_id=signal_id,
        )
    )

    buckets = DailyPerformanceStore(engine).for_day(NOW)
    assert len(buckets) == 1
    assert buckets[0].trades == 1
    assert buckets[0].wins == 1
    assert buckets[0].pnl == Decimal("47.60")
    events = ExecutionEventStore(engine).count_by_kind()
    assert events.get(ExecutionEventKind.POSITION_CLOSED.value) == 1


def test_the_loop_publishes_the_ea_state_every_interval(engine: Engine, tmp_path: Path) -> None:
    from tradingagent.ea.bridge import PROTOCOL_VERSION, read_json, state_path

    loop, _, _, _ = build_loop(engine, ea_directory=tmp_path)
    asyncio.run(loop.run_once())

    payload = read_json(state_path(tmp_path, "XAUUSD"))
    assert payload is not None
    assert payload["protocol_version"] == PROTOCOL_VERSION
    assert payload["symbol"] == "XAUUSD"
    assert payload["kill_switch"] is False
    assert payload["positions"] == []


def test_an_ea_that_stops_beating_is_reported_offline(engine: Engine, tmp_path: Path) -> None:
    loop, _, _, _ = build_loop(engine, ea_directory=tmp_path)
    asyncio.run(loop.run_once())  # creates the state directory

    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "XAUUSD_report.json").write_text(
        json.dumps(
            {
                "protocol_version": 1,
                "symbol": "XAUUSD",
                "heartbeat_at": "2026-10-06T10:00:00+00:00",
                "connected": True,
            }
        ),
        encoding="utf-8",
    )

    asyncio.run(loop.run_once())

    [offline] = events_of(engine, "ea_offline")
    assert offline.symbol == "XAUUSD"  # the Guardian that went quiet, not the whole agent
    assert loop._ea_offline == {"XAUUSD"}


# --- the weekend the agent starts into ------------------------------------------------------
#
# 2026-10-03 is a Saturday, 2026-10-05 the Monday after. The loop must announce the gold
# closure once, stop gold, and leave bitcoin alone.

SATURDAY = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
MONDAY = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def test_a_weekend_start_announces_the_gold_closure_once(engine: Engine) -> None:
    calendars = {"XAUUSD": weekend_calendar("XAUUSD"), "BTCUSD": open_calendar(BTC)}
    loop, _, _, sent = build_loop(
        engine,
        calendars=calendars,
        subscriptions=(
            Subscription("XAUUSD", Timeframe.M15),
            Subscription("BTCUSD", Timeframe.M15),
        ),
        now=SATURDAY,
    )

    asyncio.run(loop.run_once())
    asyncio.run(loop.run_once())

    closures = [text for text in sent.messages if "Marché fermé" in text]
    assert len(closures) == 1
    assert "XAUUSD" in closures[0]
    assert HaltStore(engine).is_halted(session_scope("XAUUSD")) is True
    assert HaltStore(engine).is_halted(session_scope("BTCUSD")) is False
    # Gold stopping for its weekend is not the whole agent stopping.
    assert HaltStore(engine).status().halted is False


def test_the_gold_weekend_ends_on_monday_without_any_operator_action(engine: Engine) -> None:
    calendars = {"XAUUSD": weekend_calendar("XAUUSD")}
    loop, _, _, sent = build_loop(engine, calendars=calendars, now=SATURDAY)
    asyncio.run(loop.run_once())
    assert HaltStore(engine).is_halted(session_scope("XAUUSD")) is True

    loop, _, _, sent = build_loop(engine, calendars=calendars, now=MONDAY)
    asyncio.run(loop.run_once())

    assert HaltStore(engine).is_halted(session_scope("XAUUSD")) is False
    assert any("Marché rouvert" in text for text in sent.messages)
