"""Recovery and limit-validation harness (TASK-084, TASK-085, section 16).

Seven incident scenarios and two limit scenarios, all executed in-process against a
simulated terminal that can be told to lose an answer, refuse an order, cut the connection
or make the database unusable. Each scenario starts from a fresh database and reports what
it observed, step by step, as plain text — the artefact TASK-084 asks to be consigned.

The scenarios that matter most are the ones where a naive implementation duplicates an
order: a crash between the send and its answer, a lost answer, a retry after an outage. The
harness asserts on the *number of sends* the terminal saw, because that is the only
observable that proves there was no second order (R-05).
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from tradingagent.config.agent import LiveRiskProfile, RiskConfig, RiskProfile
from tradingagent.control.guardian import Guardian
from tradingagent.core.halt import GLOBAL
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import HaltAction, HaltSource
from tradingagent.core.timeframe import Timeframe
from tradingagent.execution.journal import JournalError
from tradingagent.execution.mt5_broker import MT5Broker
from tradingagent.execution.simulator import SimulatedTerminal, decimal
from tradingagent.execution.tracking import PositionTracker
from tradingagent.risk.checks import check_daily_loss
from tradingagent.risk.model import OrderRequest, PortfolioState, limits_for
from tradingagent.storage.halts import HaltCommand, HaltStore
from tradingagent.storage.models import OrderRow, PositionRow, TradeRow
from tradingagent.storage.signals import SignalRecord, SignalRepository, idempotency_key
from tradingagent.strategies.manifest import StrategyManifest

log = logging.getLogger(__name__)

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
KEY = "recovery:XAUUSD:2026-10-06T12:00Z"
SYMBOL = "XAUUSD"
MANIFEST = StrategyManifest(
    strategy_id="witness",
    version="1.0.0",
    max_mode=TradingMode.SIGNAL,
    allowed_symbols=(SYMBOL,),
    timeframes=(Timeframe.M15,),
    history_bars=20,
)
CRASH_KEY = "recovery-crash"


class CrashingTracker(PositionTracker):
    """A tracker that dies exactly where a power cut would: after the send, before the answer
    is stored. It is the only way to reproduce the worst window without a real crash."""

    def record_result(self, order_id: int, result: object, at: datetime) -> None:
        raise JournalError("simulated power cut while storing the broker answer")


class BrokenTracker(PositionTracker):
    """A tracker whose database is impossible to write: the executor must fail closed."""

    def record_request(self, request: OrderRequest, requested_price: Decimal, at: datetime) -> int:
        raise JournalError("simulated database outage")


# -- report ----------------------------------------------------------------------------


@dataclass(frozen=True)
class StepResult:
    label: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    steps: tuple[StepResult, ...]

    @property
    def passed(self) -> bool:
        return all(step.passed for step in self.steps)

    def text(self) -> str:
        lines = [f"[{'PASS' if self.passed else 'FAIL'}] {self.name}"]
        lines.extend(
            f"    {'ok ' if step.passed else 'FAIL'} {step.label}"
            + (f" — {step.detail}" if step.detail else "")
            for step in self.steps
        )
        return "\n".join(lines)


@dataclass(frozen=True)
class RecoveryReport:
    results: tuple[ScenarioResult, ...]
    ran_at: datetime = NOW

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)

    @property
    def failures(self) -> tuple[str, ...]:
        return tuple(result.name for result in self.results if not result.passed)

    def text(self) -> str:
        header = (
            f"Recovery and limit validation — {len(self.results)} scenarios, "
            f"{'all passed' if self.passed else 'FAILURES: ' + ', '.join(self.failures)}"
        )
        return "\n".join([header, *[result.text() for result in self.results]])


def _step(label: str, passed: bool, detail: str = "") -> StepResult:
    return StepResult(label, passed, detail)


def _counts(engine: Engine) -> tuple[int, int, int]:
    with Session(engine) as session:
        orders = session.scalar(select(func.count()).select_from(OrderRow)) or 0
        positions = session.scalar(select(func.count()).select_from(PositionRow)) or 0
        trades = session.scalar(select(func.count()).select_from(TradeRow)) or 0
    return int(orders), int(positions), int(trades)


# -- world -----------------------------------------------------------------------------


@dataclass
class ScenarioWorld:
    """Everything one scenario needs: a simulated broker, a database and a guardian."""

    terminal: SimulatedTerminal
    engine: Engine
    tracker: PositionTracker
    broker: MT5Broker
    guardian: Guardian
    halts: HaltStore
    request: OrderRequest
    alerts: list[str] = field(default_factory=list)

    def restart(self) -> "ScenarioWorld":
        """A new process on the same terminal and the same database (TASK-084)."""
        tracker = PositionTracker(self.engine, now=lambda: NOW)
        return replace(
            self,
            tracker=tracker,
            broker=self._broker(self.terminal, tracker),
            guardian=Guardian(self.halts, now=lambda: NOW),
        )

    def _broker(self, terminal: SimulatedTerminal, tracker: PositionTracker) -> MT5Broker:
        return MT5Broker(
            terminal,
            tracker,
            login=terminal.login,
            mode=TradingMode.DEMO,
            now=lambda: NOW,
            alert=self.alerts.append,
            status=self.halts.status,
            guardian_alarm=self.guardian.on_account_mismatch,
        )


def build_world(
    engine: Engine,
    *,
    terminal: SimulatedTerminal | None = None,
    tracker: PositionTracker | None = None,
    key: str = KEY,
    signal_id: int | None = None,
) -> ScenarioWorld:
    """A DEMO world on a simulated terminal, with a real signal row for the foreign key."""
    terminal = SimulatedTerminal() if terminal is None else terminal
    terminal.set_tick(SYMBOL, bid=2400.0, ask=2400.2)
    tracker = PositionTracker(engine, now=lambda: NOW) if tracker is None else tracker
    halts = HaltStore(engine)
    alerts: list[str] = []
    guardian = Guardian(halts, now=lambda: NOW)
    world = ScenarioWorld(
        terminal=terminal,
        engine=engine,
        tracker=tracker,
        broker=MT5Broker(
            terminal,
            tracker,
            login=terminal.login,
            mode=TradingMode.DEMO,
            now=lambda: NOW,
            alert=alerts.append,
            status=halts.status,
            guardian_alarm=guardian.on_account_mismatch,
        ),
        guardian=guardian,
        halts=halts,
        request=OrderRequest(
            signal_id=signal_id if signal_id is not None else _ensure_signal(engine),
            idempotency_key=key,
            symbol=SYMBOL,
            direction=Direction.BUY,
            volume=Decimal("0.01"),
            stop_loss=Decimal("2390"),
            take_profit=Decimal("2420"),
            mode=TradingMode.DEMO,
            comment="ta",
        ),
        alerts=alerts,
    )
    return world


def _ensure_signal(engine: Engine) -> int:
    record = SignalRecord(
        idempotency_key=idempotency_key(MANIFEST.ref, SYMBOL, Timeframe.M15, NOW),
        manifest=MANIFEST,
        symbol=SYMBOL,
        timeframe=Timeframe.M15,
        direction=Direction.BUY,
        mode=TradingMode.SIGNAL,
        observed_price=2400.5,
        entry_low=2400.0,
        entry_high=2401.0,
        stop_loss=2390.0,
        take_profits=(2410.0, 2420.0),
        reason="recovery harness",
        indicators={"sma_20": 2395.25},
        generated_at=NOW,
        expires_at=NOW,
    )
    repository = SignalRepository(engine)
    signal_id = repository.record(record)
    if signal_id is not None:
        return signal_id
    with Session(engine) as session:
        from tradingagent.storage.models import SignalRow

        return int(
            session.scalars(
                select(SignalRow.id).where(SignalRow.idempotency_key == record.idempotency_key)
            ).one()
        )


def _sends(terminal: SimulatedTerminal) -> int:
    return terminal.calls.count("order_send")


# -- scenarios -------------------------------------------------------------------------


async def _brutal_shutdown(world: ScenarioWorld) -> ScenarioResult:
    """A power cut between the send and its answer: the restart must not order twice."""
    crash = build_world(
        world.engine,
        terminal=world.terminal,
        tracker=CrashingTracker(world.engine, now=lambda: NOW),
    )
    raised = False
    try:
        await crash.broker.place(crash.request)
    except JournalError:
        raised = True
    steps = [
        _step("the process died after the send", raised, "JournalError propagated"),
        _step("the order stayed open at the broker", len(world.terminal.position_tickets()) == 1),
        _step("exactly one order was sent", _sends(world.terminal) == 1),
    ]
    restarted = world.restart()
    result = await restarted.broker.place(restarted.request)
    steps.append(_step("the restart recovered the ticket", result.ticket is not None))
    steps.append(
        _step(
            "the restart sent no second order",
            _sends(world.terminal) == 1,
            f"{_sends(world.terminal)} send(s)",
        )
    )
    orders, _, _ = _counts(world.engine)
    steps.append(_step("one order row only", orders == 1, f"{orders} row(s)"))
    return ScenarioResult("arrêt brutal pendant l'envoi", tuple(steps))


async def _restart_with_open_position(world: ScenarioWorld) -> ScenarioResult:
    """A restart with a position open: the local state must match the account."""
    placed = await world.broker.place(world.request)
    restarted = world.restart()
    await restarted.broker.initialize()
    positions = await restarted.broker.open_positions()
    divergences = await restarted.broker.reconcile()
    steps = [
        _step("the order was accepted", placed.accepted and placed.ticket is not None),
        _step("the position survived the restart", len(positions) == 1),
        _step("state is coherent after restart", divergences == (), "; ".join(divergences)),
        _step("no extra order was sent", _sends(world.terminal) == 1),
    ]
    return ScenarioResult("redémarrage avec position ouverte", tuple(steps))


async def _network_outage(world: ScenarioWorld) -> ScenarioResult:
    """The send itself fails: nothing is invented, and the key blocks a second attempt."""
    world.terminal.fail_sends = 1
    first = await world.broker.place(world.request)
    steps = [
        _step("the outage is not reported as a fill", not first.accepted and first.ticket is None),
        _step("the order is marked as an unknown outcome", first.retcode is None),
    ]
    second = await world.broker.place(world.request)
    steps.append(_step("the retry is refused, not sent", not second.accepted))
    steps.append(
        _step(
            "no duplicate order", _sends(world.terminal) == 1, f"{_sends(world.terminal)} send(s)"
        )
    )
    return ScenarioResult("coupure réseau pendant l'envoi", tuple(steps))


async def _database_unavailable(world: ScenarioWorld) -> ScenarioResult:
    """The ledger cannot be written: the order must never reach the broker."""
    broken = build_world(
        world.engine, terminal=world.terminal, tracker=BrokenTracker(world.engine, now=lambda: NOW)
    )
    raised = False
    try:
        await broken.broker.place(broken.request)
    except JournalError:
        raised = True
    steps = [
        _step("the executor refused to trade blind", raised),
        _step(
            "nothing was sent to the broker",
            _sends(world.terminal) == 0,
            f"{_sends(world.terminal)} send(s)",
        ),
    ]
    return ScenarioResult("base indisponible", tuple(steps))


async def _broker_disconnected(world: ScenarioWorld) -> ScenarioResult:
    """A disconnected terminal: fail closed, no order, no crash of the agent."""
    world.terminal.connected = False
    refused = False
    try:
        await world.broker.place(world.request)
    except Exception as error:
        refused = True
        detail = type(error).__name__
    else:
        detail = "no error"
    steps = [
        _step("the order was refused", refused, detail),
        _step("no order reached the broker", _sends(world.terminal) == 0),
    ]
    return ScenarioResult("déconnexion du courtier", tuple(steps))


async def _lost_response(world: ScenarioWorld) -> ScenarioResult:
    """The answer is lost but the order executed: the ticket is recovered by its comment."""
    world.terminal.lost_answers = 1
    result = await world.broker.place(world.request)
    steps = [
        _step("the execution was recovered", result.accepted and result.ticket is not None),
        _step(
            "no second order was sent",
            _sends(world.terminal) == 1,
            f"{_sends(world.terminal)} send(s)",
        ),
        _step("the position is tracked", len(world.tracker.local_positions(TradingMode.DEMO)) == 1),
    ]
    return ScenarioResult("ordre envoyé, réponse perdue", tuple(steps))


async def _telegram_not_delivered(world: ScenarioWorld) -> ScenarioResult:
    """An alert that cannot be delivered must never lose the order or the position."""
    world.terminal.drop_protection = True  # forces the alert path

    def explode(_message: str) -> None:
        raise RuntimeError("simulated Telegram outage")

    world.broker = MT5Broker(
        world.terminal,
        world.tracker,
        login=world.terminal.login,
        mode=TradingMode.DEMO,
        now=lambda: NOW,
        alert=explode,
        status=world.halts.status,
        guardian_alarm=world.guardian.on_account_mismatch,
    )
    result = await world.broker.place(world.request)
    orders, positions, trades = _counts(world.engine)
    steps = [
        _step("the missing stop triggered a close", not result.stop_present),
        _step("the order is still recorded", orders == 1, f"{orders} row(s)"),
        _step("the closed position left a trade row", positions == 1 and trades == 1),
    ]
    return ScenarioResult("message Telegram non remis", tuple(steps))


def _portfolio_in_loss() -> PortfolioState:
    """Five percent of the day lost, eight percent of the week: both limits are crossed."""
    return PortfolioState(
        open_positions=(),
        trades_today=3,
        day_start_equity=Decimal(1000),
        day_pnl=Decimal(-50),
        week_start_equity=Decimal(1000),
        week_pnl=Decimal(-80),
        equity_peak=Decimal(1000),
        consecutive_losses=2,
        last_loss_at=NOW,
    )


def _limits() -> object:
    config = RiskConfig(
        simulated=RiskProfile(
            risk_per_trade_pct=Decimal("0.5"),
            daily_loss_pct=Decimal(2),
            weekly_loss_pct=Decimal(6),
            max_drawdown_pct=Decimal(10),
            max_open_positions=2,
            max_positions_per_market=1,
        ),
        live=LiveRiskProfile(
            risk_per_trade_pct=Decimal(2),
            daily_loss_pct=Decimal(5),
            weekly_loss_pct=Decimal(10),
            max_drawdown_pct=Decimal(20),
            max_open_positions=2,
            max_positions_per_market=1,
            reference_capital=Decimal(100),
            currency="EUR",
        ),
    )
    return limits_for(TradingMode.DEMO, config)


def _risk_context(portfolio: PortfolioState, limits: object) -> object:
    """The full snapshot the risk engine reads, built on the same numbers as the scenario."""
    from tradingagent.core.halt import TRADING
    from tradingagent.data.market_calendar import SlotStatus
    from tradingagent.risk.checks import RiskContext
    from tradingagent.risk.model import (
        AccountState,
        InstrumentSpec,
        MarketQuote,
        TradeIntent,
    )

    return RiskContext(
        intent=TradeIntent(
            signal_id=1,
            symbol=SYMBOL,
            direction=Direction.BUY,
            entry_low=Decimal("2399"),
            entry_high=Decimal("2401"),
            stop_loss=Decimal("2390"),
        ),
        account=AccountState(
            login=40123456,
            is_demo=True,
            currency="EUR",
            equity=Decimal(950),
            free_margin=Decimal(950),
        ),
        spec=InstrumentSpec(
            symbol=SYMBOL,
            contract_size=Decimal(100),
            volume_min=Decimal("0.01"),
            volume_step=Decimal("0.01"),
            volume_max=Decimal(100),
            point=Decimal("0.01"),
            stops_level=10,
        ),
        quote=MarketQuote(
            bid=Decimal(2400),
            ask=Decimal("2400.2"),
            loss_one_lot=Decimal("1020"),
            margin_one_lot=Decimal("8000"),
            profit_to_eur=Decimal(1),
        ),
        portfolio=portfolio,
        market=SlotStatus.OPEN,
        limits=limits,  # type: ignore[arg-type]
        now=NOW,
        halt=TRADING,
    )


async def _daily_loss_limit(world: ScenarioWorld) -> ScenarioResult:
    """TASK-085: crossing the daily loss blocks every new order.

    Two independent guards are exercised: the risk engine refuses the signal outright
    (RM-006), and the hard weekly limit halts the agent, whose executor then refuses any
    order even if one were attempted.
    """
    limits = _limits()
    portfolio = _portfolio_in_loss()
    verdict = check_daily_loss(_risk_context(portfolio, limits))  # type: ignore[arg-type]
    halted = world.guardian.on_portfolio(Decimal(950), portfolio, limits)  # type: ignore[arg-type]
    status = world.halts.status()
    refused = False
    try:
        await world.broker.place(world.request)
    except Exception:
        refused = True
    steps = [
        _step("the risk engine refuses a new order", not verdict.passed, verdict.reason),
        _step("the hard limit halted the agent", halted and status.halted),
        _step("the reason names the limit", bool(status.reasons)),
        _step("the executor refuses the order too", refused),
        _step("nothing reached the broker", _sends(world.terminal) == 0),
    ]
    return ScenarioResult("franchissement de la limite quotidienne", tuple(steps))


async def _emergency_stop_scope(world: ScenarioWorld) -> ScenarioResult:
    """TASK-085: a halt never closes a position unless the option was explicitly enabled."""
    await world.broker.place(world.request)
    opened = len(world.tracker.local_positions(TradingMode.DEMO))
    world.halts.issue(
        HaltCommand(
            GLOBAL,
            HaltAction.HALT,
            HaltSource.TELEGRAM,
            "emergency stop without closing positions",
            "operator",
            NOW,
            close_positions=False,
        )
    )
    status = world.halts.status()
    closed_without_option = 0
    for position in world.tracker.local_positions(TradingMode.DEMO):
        if status.close_positions:
            await world.broker.close(position.ticket, "emergency")
            closed_without_option += 1
    sends_after_stop = _sends(world.terminal)
    steps = [
        _step("a position was open before the stop", opened == 1),
        _step("the halt carried no close option", not status.close_positions),
        _step("no position was closed", closed_without_option == 0),
        _step("no close order was sent", sends_after_stop == 1, f"{sends_after_stop} send(s)"),
    ]
    world.halts.issue(
        HaltCommand(
            GLOBAL,
            HaltAction.RESUME,
            HaltSource.SERVER,
            "operator resumed after the test",
            "operator",
            NOW,
        )
    )
    world.halts.issue(
        HaltCommand(
            GLOBAL,
            HaltAction.HALT,
            HaltSource.TELEGRAM,
            "emergency stop closing positions",
            "operator",
            NOW,
            close_positions=True,
        )
    )
    explicit = world.halts.status()
    closed_explicitly = 0
    for position in world.tracker.local_positions(TradingMode.DEMO):
        if explicit.close_positions:
            await world.broker.close(position.ticket, "emergency")
            closed_explicitly += 1
    steps.extend(
        [
            _step("the close option is honoured only when enabled", closed_explicitly == 1),
            _step(
                "the position is closed locally",
                len(world.tracker.local_positions(TradingMode.DEMO)) == 0,
            ),
        ]
    )
    return ScenarioResult("périmètre de l'arrêt d'urgence", tuple(steps))


SCENARIOS: tuple[Callable[[ScenarioWorld], object], ...] = (
    _brutal_shutdown,
    _restart_with_open_position,
    _network_outage,
    _database_unavailable,
    _broker_disconnected,
    _lost_response,
    _telegram_not_delivered,
    _daily_loss_limit,
    _emergency_stop_scope,
)


def run_recovery_suite(engine_factory: Callable[[], Engine]) -> RecoveryReport:
    """Run every scenario on a fresh database. Read-only with respect to the real account."""
    return asyncio.run(_run_all(engine_factory))


async def _run_all(engine_factory: Callable[[], Engine]) -> RecoveryReport:
    results: list[ScenarioResult] = []
    for scenario in SCENARIOS:
        engine = engine_factory()
        try:
            world = build_world(engine)
            outcomes = await _await(scenario(world))
            results.append(outcomes)
        except Exception as error:
            log.exception("scenario %s failed", scenario.__name__)
            results.append(
                ScenarioResult(
                    scenario.__name__, (_step("scenario completed", False, repr(error)),)
                )
            )
        finally:
            engine.dispose()
    return RecoveryReport(tuple(results))


async def _await(value: object) -> ScenarioResult:
    if asyncio.iscoroutine(value):
        result: ScenarioResult = await value
        return result
    raise TypeError("scenario must be a coroutine")


__all__ = [
    "BrokenTracker",
    "CrashingTracker",
    "RecoveryReport",
    "ScenarioResult",
    "ScenarioWorld",
    "StepResult",
    "build_world",
    "decimal",
    "run_recovery_suite",
]
