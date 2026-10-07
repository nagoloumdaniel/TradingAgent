"""The agent's main loop (TASK-034 wiring, F-001, F-003, F-005, F-024).

One cycle: check the clock and the connection, poll the closed candles, store them, let
the generator evaluate the strategies, run the pipeline on every recorded signal, follow
the positions the broker closed, check the guardian's hard limits, write an account
snapshot and let the report service catch up. Nothing in a cycle raises out of it: a
failing component is counted, alerted, and the next cycle tries again.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, MutableMapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import Engine

from tradingagent.config.agent import AgentConfig
from tradingagent.config.strategy_catalog import LoadedStrategy
from tradingagent.control.guardian import Guardian
from tradingagent.core.market import Candle
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import Severity, SignalState
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.history import HistorySync
from tradingagent.data.market_calendar import MarketCalendar, learn_calendar
from tradingagent.data.market_data import ClockMismatchError, Subscription
from tradingagent.notify.health_alerts import HealthAlerter
from tradingagent.reporting.service import ReportService
from tradingagent.risk.model import ClosedPosition, limits_for
from tradingagent.runtime.pipeline import ProcessOutcome, SignalPipeline
from tradingagent.runtime.portfolio import PortfolioBuilder
from tradingagent.runtime.ports import BrokerPort, MarketPort
from tradingagent.signals.generator import GenerationStatus, SignalGenerator
from tradingagent.storage.account import AccountStore
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.events import SystemEventStore
from tradingagent.storage.halts import HaltStore
from tradingagent.storage.signals import transition

log = logging.getLogger(__name__)

# The calendar learns over eight weeks of quarter-hour slots: 8 * 7 * 96.
CALENDAR_BARS = 8 * 7 * 96 + 96
DEFAULT_POLL_SECONDS = 20.0
DEFAULT_CONNECTION_THRESHOLD_MINUTES = 10
SNAPSHOT_EVERY_CYCLES = 15  # one account snapshot every five minutes at 20 s per cycle


@dataclass
class CycleReport:
    publications: int = 0
    signals: int = 0
    outcomes: list[ProcessOutcome] = field(default_factory=list)
    closed_positions: int = 0
    divergences: int = 0
    reconnected: bool = False
    connection_lost: bool = False
    halted: bool = False


class AgentLoop:
    def __init__(
        self,
        *,
        engine: Engine,
        market: MarketPort,
        store: CandleStore,
        history: HistorySync,
        generator: SignalGenerator,
        pipeline: SignalPipeline,
        broker: BrokerPort,
        halts: HaltStore,
        guardian: Guardian,
        alerts: HealthAlerter,
        reports: ReportService,
        portfolio: PortfolioBuilder,
        config: AgentConfig,
        mode: TradingMode,
        expected_login: int,
        subscriptions: Iterable[Subscription],
        calendars: MutableMapping[str, MarketCalendar] | None = None,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        connection_threshold: timedelta = timedelta(minutes=DEFAULT_CONNECTION_THRESHOLD_MINUTES),
        now: Callable[[], datetime],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._engine = engine
        self._market = market
        self._store = store
        self._history = history
        self._generator = generator
        self._pipeline = pipeline
        self._broker = broker
        self._halts = halts
        self._guardian = guardian
        self._alerts = alerts
        self._reports = reports
        self._portfolio = portfolio
        self._config = config
        self._mode = mode
        self._expected_login = expected_login
        self._subscriptions = tuple(subscriptions)
        self._poll_seconds = poll_seconds
        self._connection_threshold = connection_threshold
        self._now = now
        self._sleep = sleep
        self._cycles = 0
        self._stop = False
        # Shared with the pipeline: the same learned calendars, updated in place.
        self._calendars = calendars if calendars is not None else {}
        self._snapshots = AccountStore(engine)
        self._events = SystemEventStore(engine)
        self._connection_loss_since: datetime | None = None

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(subscription.symbol for subscription in self._subscriptions))

    def calendar_for(self, symbol: str) -> MarketCalendar | None:
        return self._calendars.get(symbol)

    async def startup(self) -> set[str]:
        """Select the whitelist, warm the history up and learn the trading hours."""
        wanted = set(self.symbols)
        selected = await self._market.select(sorted(wanted))
        missing = wanted - selected
        if missing:
            self._events.record(
                "symbol_unavailable",
                Severity.CRITICAL,
                {"symbols": sorted(missing)},
                self._now(),
            )
        for subscription in self._subscriptions:
            if subscription.symbol not in selected:
                continue
            warmup = max(self._warmup_bars(subscription.symbol), CALENDAR_BARS)
            await self._history.sync(subscription.symbol, subscription.timeframe, warmup)
        for symbol in sorted(selected):
            self._learn(symbol)
        return selected

    async def run_forever(self) -> None:
        await self._alerts.process_started(self._now(), str(self._mode))
        try:
            while not self._stop:
                await self.run_once()
                await self._sleep(self._poll_seconds)
        finally:
            await self._alerts.process_stopping(self._now())

    def stop(self) -> None:
        self._stop = True

    async def run_once(self) -> CycleReport:
        report = CycleReport()
        now = self._now()
        self._cycles += 1
        try:
            await self._market.verify_clock()
        except ClockMismatchError as error:
            log.critical("server clock mismatch: %s", error)
            self._events.record("clock_mismatch", Severity.CRITICAL, {"detail": str(error)}, now)
            await self._alerts.connection_lost("server_clock", now)
            return report

        connected = await self._market.ensure_connected(max_attempts=1)
        if not connected:
            report.connection_lost = True
            if self._connection_loss_since is None:
                self._connection_loss_since = now
                await self._alerts.connection_lost("terminal", now)
            halted = self._guardian.on_connection_lost(
                self._connection_loss_since, self._connection_threshold
            )
            report.halted = halted
            return report
        if self._connection_loss_since is not None:
            self._connection_loss_since = None
            self._guardian.on_data_healthy()
            await self._alerts.connection_restored("terminal", now)
            report.reconnected = True

        await self._retry_notifications()
        for subscription in self._subscriptions:
            await self._poll(subscription, now, report)

        await self._reconcile(now, report)
        await self._check_limits(now, report)
        if self._cycles % SNAPSHOT_EVERY_CYCLES == 0:
            await self._snapshot(now)
        await self._run_reports(now)
        return report

    async def _poll(self, subscription: Subscription, now: datetime, report: CycleReport) -> None:
        try:
            candles = await self._market.poll_new([subscription])
        except Exception as error:
            log.warning("%s not polled: %s", subscription.symbol, error)
            await self._alerts.component_failure("market_data", str(error), now)
            return
        await self._alerts.component_recovered("market_data", now)
        report.publications += len(candles)
        for candle in candles:
            self._store.save(subscription.symbol, [candle], now)
            await self._on_candle(subscription.symbol, candle, now, report)

    async def _on_candle(
        self, symbol: str, candle: Candle, now: datetime, report: CycleReport
    ) -> None:
        try:
            closed = await self._broker.on_candle(symbol, candle)
        except Exception as error:
            log.warning("broker tracking failed on %s: %s", symbol, error)
            closed = ()
        for position in closed:
            self._record_close(position)
            report.closed_positions += 1

        calendar = self._calendars.get(symbol)
        if calendar is None:
            calendar = self._learn(symbol)
        if calendar is None:
            return
        last_tick = await self._market.last_tick_at(symbol)
        generations = self._generator.on_candle_closed(symbol, candle, calendar, now, last_tick)
        for generation in generations:
            if generation.status is not GenerationStatus.RECORDED or generation.signal_id is None:
                continue
            report.signals += 1
            try:
                outcome = await self._pipeline.process(generation.signal_id)
            except Exception as error:
                log.exception("pipeline failed on signal %d", generation.signal_id)
                self._events.record(
                    "pipeline_failed",
                    Severity.CRITICAL,
                    {"signal_id": generation.signal_id, "detail": repr(error)},
                    now,
                )
                continue
            report.outcomes.append(outcome)
            if outcome.kind == "account_mismatch":
                report.halted = True

    async def _reconcile(self, now: datetime, report: CycleReport) -> None:
        """Compare the local state with the broker's, every cycle (RM-014, TASK-083).

        A divergence is never repaired automatically: trading is suspended and the
        operator is alerted. The executor owns both sides of the comparison.
        """
        try:
            divergences = await self._broker.reconcile()
        except Exception as error:
            log.warning("reconciliation could not run: %s", error)
            await self._alerts.component_failure("reconciliation", str(error), now)
            return
        await self._alerts.component_recovered("reconciliation", now)
        if not divergences:
            return
        detail = "; ".join(divergences)
        self._guardian.on_divergence(detail)
        self._events.record(
            "position_divergence",
            Severity.CRITICAL,
            {"divergences": list(divergences)},
            now,
        )
        await self._alerts.connection_lost("reconciliation", now)
        report.divergences += len(divergences)

    def _record_close(self, closed: ClosedPosition) -> None:
        if closed.signal_id is None:
            return
        try:
            transition(
                self._engine,
                closed.signal_id,
                SignalState.CLOSED,
                closed.closed_at,
                closed.exit_reason,
            )
        except Exception as error:  # already closed, or not in a closable state
            log.warning("signal %d not moved to CLOSED: %s", closed.signal_id, error)

    async def _check_limits(self, now: datetime, report: CycleReport) -> None:
        try:
            account = await self._broker.account()
            positions = await self._broker.open_positions()
        except Exception as error:
            log.warning("cannot assess the limits: %s", error)
            return
        limits = limits_for(self._mode, self._config.risk)
        today = self._portfolio.trades_opened_since(
            now.replace(hour=0, minute=0, second=0, microsecond=0), now
        )
        state = self._portfolio.build(
            account=account, open_positions=positions, trades_today=today, at=now
        )
        if self._guardian.on_portfolio(account.equity, state, limits):
            report.halted = True

    async def _snapshot(self, now: datetime) -> None:
        try:
            account = await self._broker.account()
        except Exception as error:
            log.warning("cannot snapshot the account: %s", error)
            return
        equity = account.equity
        self._snapshots.record(equity=equity, balance=equity, at=now)

    async def _run_reports(self, now: datetime) -> None:
        try:
            await self._reports.run(now)
        except Exception as error:
            log.warning("report generation failed: %s", error)

    async def _retry_notifications(self) -> None:
        try:
            await self._pipeline.retry_pending()
        except Exception as error:
            log.warning("pending notifications not retried: %s", error)

    def _warmup_bars(self, symbol: str) -> int:
        bars = [
            loaded.manifest.history_bars
            for loaded in self._generator.strategies
            if symbol in loaded.manifest.allowed_symbols
        ]
        return max(bars) if bars else 0

    def strategies(self) -> tuple[LoadedStrategy, ...]:
        return self._generator.strategies

    def _learn(self, symbol: str) -> MarketCalendar | None:
        quarters = self._store.latest(symbol, Timeframe.M15, CALENDAR_BARS)
        try:
            calendar = learn_calendar(symbol, quarters, self._now(), weeks=8)
        except ValueError as error:
            log.warning("cannot learn %s trading hours: %s", symbol, error)
            return None
        self._calendars[symbol] = calendar
        log.info("%s calendar learned: %d open slots", symbol, len(calendar.open_slots))
        return calendar
