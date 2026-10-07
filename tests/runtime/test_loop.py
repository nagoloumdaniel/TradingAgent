"""The agent loop: one cycle, no terminal, everything injected."""

import asyncio
from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from tests.runtime.fakes import LOGIN, NOW, RISK, FakeBroker, FakeNotifier, candle

from tradingagent.config.agent import load_agent_config
from tradingagent.control.guardian import Guardian
from tradingagent.core.halt import CONNECTION, GLOBAL
from tradingagent.core.market import Candle
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.history import HistorySync
from tradingagent.data.market_calendar import MarketCalendar
from tradingagent.data.market_data import ClockMismatchError, Subscription
from tradingagent.notify.health_alerts import HealthAlerter
from tradingagent.reporting.service import ReportService
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


class Sent:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, text: str) -> None:
        self.messages.append(text)


def build_loop(
    engine: Engine,
    *,
    market: FakeMarket | None = None,
    broker: FakeBroker | None = None,
    connection_threshold: timedelta = timedelta(0),
) -> tuple[AgentLoop, FakeMarket, FakeBroker, Sent]:
    market = market or FakeMarket()
    broker = broker or FakeBroker()
    store = CandleStore(engine)
    halts = HaltStore(engine)
    events = SystemEventStore(engine)
    sent = Sent()
    alerts = HealthAlerter(sent, events, disk_path=ROOT)
    notifier = FakeNotifier()
    calendars: dict[str, MarketCalendar] = {}
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
        calendar_for=calendars.get,
        now=lambda: NOW,
    )
    loop = AgentLoop(
        engine=engine,
        market=market,
        store=store,
        history=HistorySync(market, store, now=lambda: NOW),
        generator=SignalGenerator((), store, SignalRepository(engine), TradingMode.SIGNAL),
        pipeline=pipeline,
        broker=broker,
        halts=halts,
        guardian=Guardian(halts, now=lambda: NOW),
        alerts=alerts,
        reports=ReportService(engine, sender=sent),
        portfolio=PortfolioBuilder(engine),
        config=config,
        mode=TradingMode.SIGNAL,
        expected_login=LOGIN,
        subscriptions=(Subscription("XAUUSD", Timeframe.M15),),
        calendars=calendars,
        connection_threshold=connection_threshold,
        now=lambda: NOW,
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
