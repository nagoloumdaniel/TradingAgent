"""TASK-083 acceptance criteria: a divergence suspends trading, and nothing is corrected."""

import asyncio
from decimal import Decimal

from sqlalchemy import Engine, select
from tests.execution.conftest import NOW, FakeLog, accept_signal, make_signal, request

from tradingagent.control.guardian import Guardian
from tradingagent.core.halt import GLOBAL
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.execution.reconciliation import Reconciler, reconcile_state
from tradingagent.execution.tracking import PositionTracker
from tradingagent.risk.model import BrokerPosition, OrderResult
from tradingagent.storage.halts import HaltStore
from tradingagent.storage.models import PositionRow

MODE = TradingMode.DEMO


def broker_position(
    ticket: int = 5001,
    volume: str = "0.01",
    stop: str = "2390",
    symbol: str = "XAUUSD",
    direction: Direction = Direction.BUY,
) -> BrokerPosition:
    return BrokerPosition(
        ticket=ticket,
        symbol=symbol,
        direction=direction,
        volume=Decimal(volume),
        open_price=Decimal("2400.2"),
        stop_loss=Decimal(stop),
        take_profit=None,
        mode=MODE,
    )


def seeded_log(ticket: int = 5001) -> FakeLog:
    log = FakeLog()
    order = request()
    order_id = log.record_request(order, Decimal("2400.2"), NOW)
    log.record_fill(
        order_id,
        order,
        OrderResult(
            accepted=True,
            ticket=ticket,
            retcode=10009,
            requested_price=Decimal("2400.2"),
            executed_price=Decimal("2400.2"),
            slippage=Decimal("0"),
            stop_present=True,
            message="ok",
        ),
        deal_ticket=1,
        at=NOW,
    )
    return log


def test_two_agreeing_states_are_balanced() -> None:
    log = seeded_log()
    assert reconcile_state(log, MODE, (broker_position(),)) == ()


def test_a_local_position_absent_at_the_broker_is_a_divergence() -> None:
    log = seeded_log()
    divergences = reconcile_state(log, MODE, ())
    assert len(divergences) == 1
    assert "open locally but absent at the broker" in divergences[0]


def test_a_broker_position_unknown_locally_is_a_divergence() -> None:
    log = FakeLog()
    divergences = reconcile_state(log, MODE, (broker_position(ticket=777),))
    assert len(divergences) == 1
    assert "exists at the broker but not locally" in divergences[0]


def test_a_volume_or_stop_drift_is_a_divergence() -> None:
    log = seeded_log()
    volume = reconcile_state(log, MODE, (broker_position(volume="0.05"),))
    stop = reconcile_state(log, MODE, (broker_position(stop="2300"),))
    assert any("volume" in item for item in volume)
    assert any("stop" in item for item in stop)


def test_a_side_drift_is_a_divergence() -> None:
    log = seeded_log()
    divergences = reconcile_state(log, MODE, (broker_position(direction=Direction.SELL),))
    assert any("side" in item for item in divergences)


def test_another_mode_is_not_compared() -> None:
    log = seeded_log()
    assert reconcile_state(log, TradingMode.PAPER, ()) == ()


def test_a_divergence_halts_the_agent_and_alerts(engine: Engine) -> None:
    signal_id = make_signal(engine)
    accept_signal(engine, signal_id)
    tracker = PositionTracker(engine, now=lambda: NOW)
    order = request(signal_id=signal_id)
    order_id = tracker.record_request(order, Decimal("2400.2"), NOW)
    tracker.record_fill(
        order_id,
        order,
        OrderResult(
            accepted=True,
            ticket=5001,
            retcode=10009,
            requested_price=Decimal("2400.2"),
            executed_price=Decimal("2400.2"),
            slippage=Decimal("0"),
            stop_present=True,
            message="ok",
        ),
        deal_ticket=1,
        at=NOW,
    )
    halts = HaltStore(engine)
    guardian = Guardian(halts, now=lambda: NOW)
    alerts: list[str] = []
    reconciler = Reconciler(tracker, mode=MODE, guardian=guardian, alert=alerts.append)

    class Broker:
        async def positions(self) -> tuple[BrokerPosition, ...]:
            return ()  # the broker has nothing: a divergence

    report = asyncio.run(reconciler.check(Broker()))

    assert not report.balanced
    assert halts.is_halted(GLOBAL) is True
    assert any("RM-014" in reason for reason in halts.status().reasons)
    assert alerts and "divergence" in alerts[0].lower()


def test_no_automatic_correction_is_applied(engine: Engine) -> None:
    """RM-014 has no exception: reconciliation reads, it never writes."""
    signal_id = make_signal(engine)
    accept_signal(engine, signal_id)
    tracker = PositionTracker(engine, now=lambda: NOW)
    order = request(signal_id=signal_id)
    order_id = tracker.record_request(order, Decimal("2400.2"), NOW)
    tracker.record_fill(
        order_id,
        order,
        OrderResult(
            accepted=True,
            ticket=5001,
            retcode=10009,
            requested_price=Decimal("2400.2"),
            executed_price=Decimal("2400.2"),
            slippage=Decimal("0"),
            stop_present=True,
            message="ok",
        ),
        deal_ticket=1,
        at=NOW,
    )
    before = _positions(engine)
    halts = HaltStore(engine)
    reconciler = Reconciler(tracker, mode=MODE, guardian=Guardian(halts, now=lambda: NOW))

    class Broker:
        async def positions(self) -> tuple[BrokerPosition, ...]:
            return ()

    asyncio.run(reconciler.check(Broker()))
    after = _positions(engine)

    assert after == before


def _positions(engine: Engine) -> list[tuple[int, str]]:
    with engine.connect() as connection:
        rows = connection.execute(select(PositionRow)).all()
    return [(row.broker_position_ticket, row.state.value) for row in rows]
