"""The ledger itself: idempotent orders, append-only fills, per-mode results (TASK-070/081)."""

from decimal import Decimal

from sqlalchemy import Engine, select
from tests.execution.conftest import NOW, closed, make_signal, request

from tradingagent.core.mode import TradingMode
from tradingagent.core.states import PositionState
from tradingagent.execution.journal import OrderJournal
from tradingagent.storage.models import ExecutionRow, OrderRow, PositionRow, TradeRow


def journal(engine: Engine) -> OrderJournal:
    return OrderJournal(engine)


def test_the_same_key_produces_one_order_row(engine: Engine) -> None:
    instance = journal(engine)
    signal_id = make_signal(engine)
    order = request(signal_id=signal_id)

    first = instance.record_request(order, Decimal("2400.2"), NOW)
    second = instance.record_request(order, Decimal("2400.2"), NOW)

    assert first == second
    with engine.connect() as connection:
        assert len(connection.execute(select(OrderRow)).all()) == 1


def test_a_fill_is_appended_once_per_deal(engine: Engine) -> None:
    instance = journal(engine)
    signal_id = make_signal(engine)
    order = request(signal_id=signal_id)
    order_id = instance.record_request(order, Decimal("2400.2"), NOW)

    instance.record_execution(
        order_id, deal_ticket=9001, price=2400.5, volume=Decimal("0.01"), slippage=0.3, at=NOW
    )
    instance.record_execution(
        order_id, deal_ticket=9001, price=2400.5, volume=Decimal("0.01"), slippage=0.3, at=NOW
    )

    with engine.connect() as connection:
        rows = connection.execute(select(ExecutionRow)).all()
    assert len(rows) == 1
    assert rows[0].broker_deal_ticket == 9001
    assert rows[0].volume == Decimal("0.01")


def test_opening_the_same_position_twice_inserts_once(engine: Engine) -> None:
    from tradingagent.risk.model import BrokerPosition

    instance = journal(engine)
    signal_id = make_signal(engine)
    order = request(signal_id=signal_id)
    order_id = instance.record_request(order, Decimal("2400.2"), NOW)
    position = BrokerPosition(
        ticket=5001,
        symbol="XAUUSD",
        direction=order.direction,
        volume=Decimal("0.01"),
        open_price=Decimal("2400.2"),
        stop_loss=Decimal("2390"),
        take_profit=None,
        mode=TradingMode.DEMO,
    )

    first = instance.open_position(order_id, position, NOW)
    second = instance.open_position(order_id, position, NOW)

    assert first is not None
    assert second is None
    with engine.connect() as connection:
        assert len(connection.execute(select(PositionRow)).all()) == 1


def test_result_marks_the_order_and_keeps_the_ticket(engine: Engine) -> None:
    from tradingagent.core.states import OrderState
    from tradingagent.risk.model import OrderResult

    instance = journal(engine)
    signal_id = make_signal(engine)
    order = request(signal_id=signal_id)
    order_id = instance.record_request(order, Decimal("2400.2"), NOW)

    instance.record_result(
        order_id,
        OrderResult(
            accepted=True,
            ticket=5001,
            retcode=10009,
            requested_price=Decimal("2400.2"),
            executed_price=Decimal("2400.5"),
            slippage=Decimal("0.3"),
            stop_present=True,
            message="done",
        ),
        NOW,
    )

    with engine.connect() as connection:
        row = connection.execute(select(OrderRow)).one()
    assert row.state is OrderState.FILLED
    assert row.broker_order_ticket == 5001
    snapshot = instance.find_order(order.idempotency_key)
    assert snapshot is not None and snapshot.ticket == 5001
    assert instance.order_by_ticket(5001) is not None


def test_realized_pnl_only_counts_the_requested_mode(engine: Engine) -> None:
    from datetime import timedelta

    from tests.execution.conftest import NOW

    from tradingagent.risk.model import BrokerPosition

    instance = journal(engine)
    for mode, ticket, at in (
        (TradingMode.DEMO, 1, NOW),
        (TradingMode.PAPER, 2, NOW + timedelta(minutes=15)),
    ):
        signal_id = make_signal(engine, mode, at)
        order = request(key=f"key-{mode}", signal_id=signal_id, mode=mode)
        order_id = instance.record_request(order, Decimal("2400.2"), NOW)
        instance.open_position(
            order_id,
            BrokerPosition(
                ticket=ticket,
                symbol="XAUUSD",
                direction=order.direction,
                volume=Decimal("0.01"),
                open_price=Decimal("2400.2"),
                stop_loss=Decimal("2390"),
                take_profit=None,
                mode=mode,
            ),
            NOW,
        )
        instance.close_position(
            closed(ticket, pnl_eur=Decimal("5") if mode is TradingMode.DEMO else Decimal("-7")),
            mode=mode,
            at=NOW,
        )

    assert instance.realized_pnl(TradingMode.DEMO) == Decimal("5")
    assert instance.realized_pnl(TradingMode.PAPER) == Decimal("-7")
    with engine.connect() as connection:
        states = [row.state for row in connection.execute(select(PositionRow)).all()]
    assert states == [PositionState.CLOSED, PositionState.CLOSED]
    with engine.connect() as connection:
        assert len(connection.execute(select(TradeRow)).all()) == 2
