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
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Engine

from tradingagent.config.agent import AgentConfig
from tradingagent.config.strategy_catalog import LoadedStrategy
from tradingagent.control.guardian import Guardian
from tradingagent.core.market import Candle
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import ExecutionEventKind, Severity, SignalState
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.history import HistorySync
from tradingagent.data.market_calendar import MarketCalendar, learn_calendar
from tradingagent.data.market_data import ClockMismatchError, Subscription
from tradingagent.ea.bridge import EaStatus, ExpectedPosition, publish_state
from tradingagent.ea.health import ea_health
from tradingagent.notify.health_alerts import HealthAlerter
from tradingagent.notify.trade_messages import PositionClosed, render_position_closed
from tradingagent.reporting.service import ReportService
from tradingagent.risk.model import BrokerPosition, ClosedPosition, limits_for
from tradingagent.runtime.pipeline import ProcessOutcome, SignalPipeline
from tradingagent.runtime.portfolio import PortfolioBuilder
from tradingagent.runtime.ports import BrokerPort, DailyLabPort, MarketPort, NotifierPort
from tradingagent.runtime.sessions import MarketSessionGuard
from tradingagent.signals.generator import GenerationStatus, SignalGenerator
from tradingagent.storage.account import AccountStore
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.daily import DailyPerformanceStore
from tradingagent.storage.events import SystemEventStore
from tradingagent.storage.halts import HaltStore
from tradingagent.storage.risk_decisions import latest_risk_eur
from tradingagent.storage.signals import get_signal, transition
from tradingagent.storage.telemetry import ExecutionEventStore

log = logging.getLogger(__name__)

# The calendar learns over eight weeks of quarter-hour slots: 8 * 7 * 96.
CALENDAR_BARS = 8 * 7 * 96 + 96
DEFAULT_POLL_SECONDS = 20.0
DEFAULT_CONNECTION_THRESHOLD_MINUTES = 10
# The Guardian EAs stop trading after 30 s without a heartbeat (EA_PROTOCOL §6).
EA_PUBLISH_INTERVAL = timedelta(seconds=20)
# The AI Lab pass is idempotent per UTC day; checking hourly is enough and keeps the loop free.
LAB_CHECK_INTERVAL = timedelta(hours=1)
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
        notifier: NotifierPort,
        guardian: Guardian,
        alerts: HealthAlerter,
        reports: ReportService,
        portfolio: PortfolioBuilder,
        config: AgentConfig,
        mode: TradingMode,
        expected_login: int,
        subscriptions: Iterable[Subscription],
        calendars: MutableMapping[str, MarketCalendar] | None = None,
        sessions: MarketSessionGuard | None = None,
        ea_directory: Path | None = None,
        ea_magic: int = 0,
        lab: "DailyLabPort | None" = None,
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
        self._notifier = notifier
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
        # Stops the markets the calendar says are closed, and reopens them on its own.
        self._sessions = (
            sessions
            if sessions is not None
            else MarketSessionGuard(halts=halts, alerts=alerts, calendar_for=self.calendar_for)
        )
        # The MQL5 Guardians stop trading on their own when the heartbeat goes quiet, so the
        # state must be republished well inside their timeout (EA_PROTOCOL §6).
        self._ea_directory = ea_directory
        self._ea_magic = ea_magic
        self._ea_last_publish: datetime | None = None
        self._ea_offline: set[str] = set()
        # The AI Lab pass is idempotent per UTC day; checking hourly keeps the loop simple.
        self._lab = lab
        self._lab_last_check: datetime | None = None
        self._snapshots = AccountStore(engine)
        self._events = SystemEventStore(engine)
        self._telemetry = ExecutionEventStore(engine)
        self._daily = DailyPerformanceStore(engine)
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

        # The connection is restored BEFORE the clock is verified, and that order is not
        # cosmetic: `connect()` verifies the clock itself, so checking it first made a
        # terminal restart permanent. `last_tick` returned None on a handle whose terminal
        # had been restarted, the cycle returned, and nothing ever reconnected — the agent
        # could not survive the operator restarting MetaTrader. Observed live on 2026-10-07.
        try:
            connected = await self._market.ensure_connected(max_attempts=1)
        except ClockMismatchError as error:
            return await self._clock_failed(error, now, report)
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

        try:
            await self._market.verify_clock()
        except ClockMismatchError as error:
            return await self._clock_failed(error, now, report)

        await self._guard_sessions(now)
        await self._retry_notifications()
        for subscription in self._subscriptions:
            await self._poll(subscription, now, report)

        await self._reconcile(now, report)
        await self._sync_eas(now)
        await self._run_lab(now)
        await self._check_limits(now, report)
        if self._cycles % SNAPSHOT_EVERY_CYCLES == 0:
            await self._snapshot(now)
        await self._run_reports(now)
        return report

    async def _clock_failed(
        self, error: ClockMismatchError, now: datetime, report: CycleReport
    ) -> CycleReport:
        """Refuse the cycle when the server clock cannot be trusted.

        Converting a timestamp with an offset we cannot verify would shift every candle, so
        the agent stops rather than trade on a wrong clock. The state is recorded and the
        operator alerted; the next cycle tries again.
        """
        log.critical("server clock mismatch: %s", error)
        self._events.record("clock_mismatch", Severity.CRITICAL, {"detail": str(error)}, now)
        await self._alerts.connection_lost("server_clock", now)
        return report

    async def _guard_sessions(self, now: datetime) -> None:
        """Stop what the calendar says is closed, reopen it once it trades again (F-005).

        One market at a time: a failure on gold must not stop the crypto, and vice versa.
        Gold's weekend is not the agent's weekend, so the halt lives in `session:XAUUSD`,
        which the risk engine never reads as a global stop.
        """
        for symbol in self.symbols:
            try:
                await self._sessions.guard(symbol, now)
            except Exception as error:
                log.warning("%s session guard failed: %s", symbol, error)

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
            await self.notify_close(position)
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
                    symbol=symbol,
                )
                continue
            report.outcomes.append(outcome)
            if outcome.kind == "account_mismatch":
                report.halted = True

    async def _run_lab(self, now: datetime) -> None:
        """One AI Lab pass a day: analysis of the closed trades, then hypotheses.

        The pass writes analyses and proposals only, so it can never affect an order. Its
        idempotence lives in the pass itself (a per-day marker), which is why a failure here
        is only logged: the next cycle tries again.
        """
        if self._lab is None:
            return
        if self._lab_last_check is not None and now - self._lab_last_check < LAB_CHECK_INTERVAL:
            return
        self._lab_last_check = now
        try:
            await self._lab.run_once(now)
        except Exception as error:
            log.warning("AI Lab pass failed: %s", error)
            self._events.record("ai_lab_failed", Severity.WARNING, {"detail": repr(error)}, now)

    async def _sync_eas(self, now: datetime) -> None:
        """Publish the expected state to the Guardian EAs and watch their heartbeat.

        The EA stops trading on its own when the backend goes quiet, so this runs well
        inside its timeout. A bridge failure is logged and never breaks the cycle: the
        terminal is a safety net, not a dependency of the trading engine.
        """
        if self._ea_directory is None:
            return
        directory = self._ea_directory
        due = self._ea_last_publish is None or now - self._ea_last_publish >= EA_PUBLISH_INTERVAL
        if due:
            try:
                positions = await self._broker.positions()
                halt = self._halts.status()
                self._publish_ea_state(directory, positions, halt.halted)
                self._ea_last_publish = now
            except Exception as error:
                log.warning("cannot publish the EA state: %s", error)
        await self._check_ea_health(directory, now)

    def _publish_ea_state(
        self,
        directory: Path,
        positions: tuple[BrokerPosition, ...],
        halted: bool,
    ) -> None:
        for symbol in self.symbols:
            expected = tuple(
                ExpectedPosition(
                    ticket=position.ticket,
                    direction=position.direction,
                    volume=float(position.volume),
                    stop_loss=(
                        float(position.stop_loss) if position.stop_loss is not None else None
                    ),
                    take_profit=(
                        float(position.take_profit) if position.take_profit is not None else None
                    ),
                    comment="",
                )
                for position in positions
                if position.symbol == symbol
            )
            publish_state(
                directory,
                symbol,
                magic=self._ea_magic,
                positions=expected,
                kill_switch=halted,
                max_positions=self._config.risk.simulated.max_positions_per_market,
                now=self._now,
            )

    async def _check_ea_health(self, directory: Path, now: datetime) -> None:
        health = ea_health(directory / "reports", now=self._now)
        for symbol, status in health.items():
            if status is EaStatus.OFFLINE and symbol not in self._ea_offline:
                self._ea_offline.add(symbol)
                self._events.record(
                    "ea_offline", Severity.WARNING, {"symbol": symbol}, now, symbol=symbol
                )
                await self._alerts.component_failure(
                    f"ea:{symbol}", "heartbeat older than the timeout", now
                )
            elif status is EaStatus.ONLINE and symbol in self._ea_offline:
                self._ea_offline.discard(symbol)
                await self._alerts.component_recovered(f"ea:{symbol}", now)

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
        self._record_close_telemetry(closed)

    def _record_close_telemetry(self, closed: ClosedPosition) -> None:
        """Telemetry and the daily bucket the dashboard reads (cahier v3 §20, §31)."""
        detail = get_signal(self._engine, closed.signal_id) if closed.signal_id else None
        ref = detail.strategy_ref if detail is not None else "unknown"
        mode = detail.mode if detail is not None else self._mode
        risk = latest_risk_eur(self._engine, closed.signal_id) if closed.signal_id else Decimal(0)
        try:
            self._telemetry.record(
                ExecutionEventKind.POSITION_CLOSED,
                closed.symbol,
                {
                    "ticket": closed.ticket,
                    "pnl_eur": str(closed.pnl_eur),
                    "risk_eur": str(risk),
                    "exit_reason": closed.exit_reason,
                },
                closed.closed_at,
                signal_id=closed.signal_id,
            )
            self._daily.apply_trade(
                day=closed.closed_at,
                mode=mode,
                market=closed.symbol,
                ref=ref,
                pnl=closed.pnl_eur,
                risk_eur=risk,
                won=closed.pnl_eur > 0,
                at=closed.closed_at,
            )
        except Exception as error:  # telemetry must never break the close path
            log.warning("close telemetry failed for ticket %s: %s", closed.ticket, error)

    async def notify_close(self, closed: ClosedPosition) -> None:
        """`+10.00 €` / `-10.00 €` and the total balance, nothing else (cahier v3, §37)."""
        currency, balance = await self._account_totals()
        notice = PositionClosed(
            symbol=closed.symbol,
            pnl=closed.pnl_eur,
            balance=balance,
            currency=currency,
            exit_reason=closed.exit_reason,
            ticket=closed.ticket,
        )
        try:
            await self._notifier.send(render_position_closed(notice), parse_mode="HTML")
        except Exception as error:  # a notification failure never blocks the loop
            log.warning("position-closed notice failed: %s", error)

    async def _account_totals(self) -> tuple[str, Decimal]:
        try:
            account = await self._broker.account()
        except Exception as error:
            log.warning("balance unavailable for the close notice: %s", error)
            return "EUR", Decimal(0)
        balance = account.balance if account.balance is not None else account.equity
        return account.currency, balance

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
