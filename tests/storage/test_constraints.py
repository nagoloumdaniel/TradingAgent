from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError, StatementError
from sqlalchemy.orm import Session

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import (
    HaltAction,
    HaltSource,
    OrderState,
    PositionState,
    Severity,
    SignalState,
)
from tradingagent.core.timeframe import Timeframe
from tradingagent.storage.models import (
    AuditLogRow,
    CandleRow,
    ExecutionRow,
    HaltCommandRow,
    OrderRow,
    PositionRow,
    SignalEventRow,
    SignalRow,
    StrategyVersionRow,
    SystemEventRow,
    TradeRow,
)

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def candle(open_time: datetime = NOW) -> CandleRow:
    return CandleRow(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        open_time=open_time,
        open=4139.0,
        high=4140.0,
        low=4138.0,
        close=4139.5,
        source="mt5",
        ingested_at=NOW,
    )


def strategy_version() -> StrategyVersionRow:
    return StrategyVersionRow(
        ref="witness@1.0.0",
        strategy_id="witness",
        version="1.0.0",
        manifest={"strategy_id": "witness"},
        content_hash="a" * 64,
        first_seen_at=NOW,
    )


def signal(version: StrategyVersionRow, key: str = "sig-1") -> SignalRow:
    return SignalRow(
        idempotency_key=key,
        strategy_version=version,
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        direction=Direction.BUY,
        mode=TradingMode.DEMO,
        observed_price=4139.0,
        entry_low=4138.5,
        entry_high=4139.5,
        stop_loss=4126.7,
        take_profits=[4163.6],
        reason="test",
        indicators={"atr": 8.2},
        generated_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        state=SignalState.CANDIDATE,
    )


def order(parent: SignalRow, key: str = "ord-1") -> OrderRow:
    return OrderRow(
        idempotency_key=key,
        signal=parent,
        symbol="XAUUSD",
        direction=Direction.BUY,
        volume=Decimal("0.02"),
        requested_price=4139.0,
        stop_loss=4126.7,
        take_profit=4163.6,
        mode=TradingMode.DEMO,
        state=OrderState.SENT,
        created_at=NOW,
        updated_at=NOW,
    )


def full_chain(session: Session) -> TradeRow:
    version = strategy_version()
    parent_signal = signal(version)
    parent_order = order(parent_signal)
    session.add_all(
        [
            SignalEventRow(signal=parent_signal, state=SignalState.CANDIDATE, occurred_at=NOW),
            ExecutionRow(
                order=parent_order,
                broker_deal_ticket=9700390355,
                price=4139.0,
                volume=Decimal("0.02"),
                executed_at=NOW,
            ),
        ]
    )
    position = PositionRow(
        broker_position_ticket=9827313194,
        order=parent_order,
        symbol="XAUUSD",
        direction=Direction.BUY,
        volume=Decimal("0.02"),
        open_price=4139.0,
        stop_loss=4126.7,
        take_profit=4163.6,
        mode=TradingMode.DEMO,
        state=PositionState.CLOSED,
        opened_at=NOW,
        updated_at=NOW,
    )
    trade = TradeRow(
        position=position,
        mode=TradingMode.DEMO,
        closed_at=NOW + timedelta(hours=1),
        close_price=4126.7,
        pnl_eur=Decimal("-21.88"),
        risk_eur=Decimal("21.88"),
        exit_reason="stop_loss",
    )
    session.add_all(
        [
            trade,
            AuditLogRow(actor="operator", action="pause", detail={}, occurred_at=NOW),
            HaltCommandRow(
                scope="global",
                action=HaltAction.HALT,
                close_positions=False,
                source=HaltSource.SERVER,
                reason="test",
                actor="operator",
                occurred_at=NOW,
            ),
        ]
    )
    session.commit()
    return trade


def test_a_complete_chain_can_be_stored(engine: Engine) -> None:
    with Session(engine) as session:
        trade = full_chain(session)
        assert trade.pnl_eur == Decimal("-21.88")
        assert trade.position.order.signal.strategy_version.ref == "witness@1.0.0"


def test_datetimes_come_back_as_aware_utc(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(candle())
        session.commit()
        session.expire_all()
        loaded = session.query(CandleRow).one()
        assert loaded.open_time == NOW
        assert loaded.open_time.tzinfo is UTC


def test_decimal_amounts_come_back_exact(engine: Engine) -> None:
    with Session(engine) as session:
        full_chain(session)
        session.expire_all()
        assert session.query(TradeRow).one().pnl_eur == Decimal("-21.88")


def test_duplicate_candle_is_rejected_by_the_database(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(candle())
        session.commit()
        session.add(candle())
        with pytest.raises(IntegrityError):
            session.commit()


def test_duplicate_signal_key_is_rejected_by_the_database(engine: Engine) -> None:
    with Session(engine) as session:
        version = strategy_version()
        session.add_all([signal(version, "same"), signal(version, "same")])
        with pytest.raises(IntegrityError):
            session.commit()


def test_duplicate_order_key_is_rejected_by_the_database(engine: Engine) -> None:
    with Session(engine) as session:
        parent = signal(strategy_version())
        session.add_all([order(parent, "same"), order(parent, "same")])
        with pytest.raises(IntegrityError):
            session.commit()


def test_duplicate_strategy_reference_is_rejected(engine: Engine) -> None:
    with Session(engine) as session:
        session.add_all([strategy_version(), strategy_version()])
        with pytest.raises(IntegrityError):
            session.commit()


def test_dangling_foreign_key_is_rejected(engine: Engine) -> None:
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text(
                "INSERT INTO signal_events (signal_id, state, occurred_at) "
                "VALUES (999, 'candidate', '2026-10-04 12:00:00')"
            )
        )


def test_unknown_enum_value_is_rejected_by_the_database(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(strategy_version())
        session.commit()
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text(
                "INSERT INTO system_events (kind, severity, detail, occurred_at) "
                "VALUES ('x', 'catastrophic', '{}', '2026-10-04 12:00:00')"
            )
        )


def test_naive_datetime_never_reaches_the_database(engine: Engine) -> None:
    naive = datetime(2026, 10, 4)  # noqa: DTZ001 - the point of the test
    with Session(engine) as session:
        session.add(SystemEventRow(kind="x", severity=Severity.INFO, detail={}, occurred_at=naive))
        with pytest.raises(StatementError, match="naive"):
            session.commit()


APPEND_ONLY = ["signal_events", "executions", "trades", "audit_log", "halt_commands"]


@pytest.mark.parametrize("table", APPEND_ONLY)
def test_append_only_table_refuses_updates(engine: Engine, table: str) -> None:
    with Session(engine) as session:
        full_chain(session)
    with engine.begin() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text(f"UPDATE {table} SET id = id"))  # noqa: S608


@pytest.mark.parametrize("table", APPEND_ONLY)
def test_append_only_table_refuses_deletes(engine: Engine, table: str) -> None:
    with Session(engine) as session:
        full_chain(session)
    with engine.begin() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text(f"DELETE FROM {table}"))  # noqa: S608


def test_mutable_tables_can_still_be_updated(engine: Engine) -> None:
    with Session(engine) as session:
        full_chain(session)
    with engine.begin() as connection:
        connection.execute(text("UPDATE orders SET state = 'filled'"))
        connection.execute(text("UPDATE positions SET state = 'closed'"))
        connection.execute(text("UPDATE signals SET state = 'closed'"))


@pytest.mark.parametrize("table", APPEND_ONLY)
def test_append_only_table_refuses_truncation_on_postgres(engine: Engine, table: str) -> None:
    if engine.dialect.name != "postgresql":
        pytest.skip("SQLite has no TRUNCATE")
    with Session(engine) as session:
        full_chain(session)
    with engine.begin() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text(f"TRUNCATE {table} CASCADE"))
