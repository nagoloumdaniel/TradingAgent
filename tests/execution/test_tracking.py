"""TASK-082 acceptance criteria: the local state follows the account and closings are exact."""

import asyncio
from dataclasses import replace
from decimal import Decimal

from sqlalchemy import Engine, select
from tests.execution.conftest import NOW, accept_signal, closed, make_signal, request

from tradingagent.core.market import Candle
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import OrderState, PositionState, SignalState
from tradingagent.core.timeframe import Timeframe
from tradingagent.execution.journal import OrderJournal
from tradingagent.execution.tracking import PositionTracker
from tradingagent.risk.model import ClosedPosition, OrderResult
from tradingagent.storage.models import ExecutionRow, OrderRow, PositionRow, TradeRow
from tradingagent.storage.signals import history, transition


def tracker(engine: Engine) -> PositionTracker:
    return PositionTracker(engine, now=lambda: NOW)


def result(ticket: int | None = 5001, **changes: object) -> OrderResult:
    base = OrderResult(
        accepted=True,
        ticket=ticket,
        retcode=10009,
        requested_price=Decimal("2400.2"),
        executed_price=Decimal("2400.5"),
        slippage=Decimal("0.3"),
        stop_present=True,
        message="done",
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_a_fill_opens_a_position_and_leaves_the_lifecycle_to_the_caller(engine: Engine) -> None:
    signal_id = make_signal(engine)
    accept_signal(engine, signal_id)
    instance = tracker(engine)
    order = request(signal_id=signal_id)
    order_id = instance.record_request(order, Decimal("2400.2"), NOW)

    position_id = instance.record_fill(order_id, order, result(), deal_ticket=9001, at=NOW)

    assert position_id is not None
    with engine.connect() as connection:
        execution = connection.execute(select(ExecutionRow)).one()
        position = connection.execute(select(PositionRow)).one()
        order_row = connection.execute(select(OrderRow)).one()
    assert execution.broker_deal_ticket == 9001
    assert execution.price == 2400.5
    assert execution.slippage == 0.3
    assert position.broker_position_ticket == 5001
    assert position.state is PositionState.OPEN
    assert order_row.state is OrderState.SENT  # a fill does not rewrite the order row
    # POSITION_OPEN belongs to the caller: at fill time the signal is still ORDER_SENT,
    # and RM-018 does not allow ORDER_SENT -> POSITION_OPEN.
    states = [event.state for event in history(engine, signal_id)]
    assert states[-1] is SignalState.ORDER_ACCEPTED


def test_a_refusal_is_stored_as_rejected(engine: Engine) -> None:
    signal_id = make_signal(engine)
    instance = tracker(engine)
    order = request(signal_id=signal_id)
    order_id = instance.record_request(order, Decimal("2400.2"), NOW)
    refused = result(ticket=None, accepted=False, retcode=10006, stop_present=False)

    instance.record_result(order_id, refused, NOW)

    with engine.connect() as connection:
        row = connection.execute(select(OrderRow)).one()
    assert row.state is OrderState.REJECTED
    assert row.retcode == 10006


def test_a_lost_answer_is_stored_as_an_error_not_a_rejection(engine: Engine) -> None:
    signal_id = make_signal(engine)
    instance = tracker(engine)
    order = request(signal_id=signal_id)
    order_id = instance.record_request(order, Decimal("2400.2"), NOW)

    instance.record_result(
        order_id, result(ticket=None, accepted=False, retcode=None, stop_present=False), NOW
    )

    with engine.connect() as connection:
        row = connection.execute(select(OrderRow)).one()
    assert row.state is OrderState.ERROR


def test_a_closure_writes_the_trade_reason_and_result(engine: Engine) -> None:
    signal_id = make_signal(engine)
    accept_signal(engine, signal_id)
    instance = tracker(engine)
    order = request(signal_id=signal_id)
    order_id = instance.record_request(order, Decimal("2400.2"), NOW)
    instance.record_fill(order_id, order, result(), deal_ticket=9001, at=NOW)
    # The caller (agent loop) moves the signal to POSITION_OPEN once the fill is confirmed.
    transition(engine, signal_id, SignalState.POSITION_OPEN, NOW)

    trade_ids = instance.record_closures(
        (closed(5001, exit_reason="stop_loss", pnl_eur=Decimal("-10.5"), signal_id=signal_id),),
        NOW,
    )

    assert len(trade_ids) == 1
    with engine.connect() as connection:
        position = connection.execute(select(PositionRow)).one()
        trade = connection.execute(select(TradeRow)).one()
    assert position.state is PositionState.CLOSED
    assert trade.exit_reason == "stop_loss"
    assert trade.pnl_eur == Decimal("-10.5")
    assert trade.mode is TradingMode.DEMO
    assert instance.realized_pnl(TradingMode.DEMO) == Decimal("-10.5")
    states = [event.state for event in history(engine, signal_id)]
    assert states[-1] is SignalState.CLOSED


def test_closing_twice_never_double_counts(engine: Engine) -> None:
    signal_id = make_signal(engine)
    accept_signal(engine, signal_id)
    instance = tracker(engine)
    order = request(signal_id=signal_id)
    order_id = instance.record_request(order, Decimal("2400.2"), NOW)
    instance.record_fill(order_id, order, result(), deal_ticket=9001, at=NOW)
    transition(engine, signal_id, SignalState.POSITION_OPEN, NOW)
    item = closed(5001, pnl_eur=Decimal("-10.5"), signal_id=signal_id)

    first = instance.record_closures((item,), NOW)
    second = instance.record_closures((item,), NOW)

    assert len(first) == 1 and second == ()
    assert instance.realized_pnl(TradingMode.DEMO) == Decimal("-10.5")
    with engine.connect() as connection:
        assert len(connection.execute(select(TradeRow)).all()) == 1


def test_a_closure_for_a_foreign_ticket_is_ignored(engine: Engine) -> None:
    instance = tracker(engine)
    assert instance.record_closures((closed(9999, signal_id=None),), NOW) == ()
    assert instance.realized_pnl(TradingMode.DEMO) == Decimal(0)


def test_local_positions_are_filtered_by_mode(engine: Engine) -> None:
    signal_id = make_signal(engine, TradingMode.DEMO)
    accept_signal(engine, signal_id)
    instance = tracker(engine)
    order = request(signal_id=signal_id)
    order_id = instance.record_request(order, Decimal("2400.2"), NOW)
    instance.record_fill(order_id, order, result(), deal_ticket=9001, at=NOW)

    assert len(instance.local_positions(TradingMode.DEMO)) == 1
    assert instance.local_positions(TradingMode.PAPER) == ()
    found = instance.position_for_ticket(5001)
    assert found is not None and found.signal_id == signal_id


def test_follow_delegates_to_the_broker_follow_calls() -> None:
    class FakeBroker:
        async def on_tick(
            self, symbol: str, bid: Decimal, ask: Decimal
        ) -> tuple[ClosedPosition, ...]:
            return (closed(1),)

        async def on_candle(self, symbol: str, candle: object) -> tuple[ClosedPosition, ...]:
            return (closed(2),)

    tracker_ = PositionTracker.__new__(PositionTracker)
    broker = FakeBroker()
    tick = asyncio.run(tracker_.follow(broker, "XAUUSD", Decimal(1), Decimal(2)))
    bar = Candle(Timeframe.M15, NOW, 1.0, 1.0, 1.0, 1.0)
    candle = asyncio.run(tracker_.follow_candle(broker, "XAUUSD", bar))

    assert tick[0].ticket == 1 and candle[0].ticket == 2


def test_net_and_summary_of_a_batch_of_closures() -> None:
    tracker_ = PositionTracker.__new__(PositionTracker)
    items = (
        closed(1, pnl_eur=Decimal("12.5"), exit_reason="take_profit"),
        closed(2, pnl_eur=Decimal("-2.5"), exit_reason="stop_loss"),
    )
    assert tracker_.net_of(items) == Decimal("10.0")
    text = tracker_.summarize(items)
    assert "take_profit" in text and "stop_loss" in text
    assert tracker_.summarize(()) == "no position closed"


def test_the_order_journal_is_reused_by_the_tracker(engine: Engine) -> None:
    journal = OrderJournal(engine)
    instance = PositionTracker(engine, journal)
    assert instance.journal is journal
