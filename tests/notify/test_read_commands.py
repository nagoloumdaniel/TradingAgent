import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import OrderState, PositionState, SignalState
from tradingagent.core.timeframe import Timeframe
from tradingagent.notify.commands import CommandRequest
from tradingagent.notify.read_commands import (
    markets_handler,
    performance_handler,
    positions_handler,
    signals_handler,
)
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import (
    CandleRow,
    OrderRow,
    PositionRow,
    SignalRow,
    StrategyVersionRow,
    TradeRow,
)

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'read.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


def a_request(command: str = "markets") -> CommandRequest:
    return CommandRequest(user_id=111, command=command, args=(), at=T0)


def run(handler: object, request: CommandRequest) -> str:
    return asyncio.run(handler(request))  # type: ignore[operator]


def seed_version(session: Session) -> StrategyVersionRow:
    version = StrategyVersionRow(
        ref="witness@1.0.0",
        strategy_id="witness",
        version="1.0.0",
        manifest={},
        content_hash="0" * 64,
        first_seen_at=T0,
    )
    session.add(version)
    session.flush()
    return version


def seed_signal(
    session: Session,
    version: StrategyVersionRow,
    key: str,
    *,
    generated_at: datetime,
    state: SignalState = SignalState.VALIDATED,
) -> SignalRow:
    row = SignalRow(
        idempotency_key=key,
        strategy_version_id=version.id,
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        direction=Direction.BUY,
        mode=TradingMode.DEMO,
        observed_price=2650.0,
        entry_low=2649.5,
        entry_high=2650.5,
        stop_loss=2647.5,
        take_profits=[2652.5],
        reason="witness crossover",
        indicators={},
        generated_at=generated_at,
        expires_at=generated_at + timedelta(minutes=45),
        state=state,
    )
    session.add(row)
    session.flush()
    return row


def seed_position(
    session: Session,
    version: StrategyVersionRow,
    ticket: int,
    *,
    symbol: str = "XAUUSD",
    state: PositionState = PositionState.OPEN,
    mode: TradingMode = TradingMode.DEMO,
) -> PositionRow:
    signal = seed_signal(
        session,
        version,
        f"witness@1.0.0:{symbol}:M15:2026-10-06T1{ticket % 10}:00Z",
        generated_at=T0 - timedelta(hours=ticket),
    )
    order = OrderRow(
        idempotency_key=f"order-{ticket}",
        signal_id=signal.id,
        symbol=symbol,
        direction=Direction.BUY,
        volume=Decimal("0.01"),
        requested_price=2650.0,
        stop_loss=2647.5,
        take_profit=2652.5,
        mode=mode,
        state=OrderState.FILLED,
        created_at=T0 - timedelta(hours=ticket),
        updated_at=T0 - timedelta(hours=ticket),
    )
    session.add(order)
    session.flush()
    position = PositionRow(
        broker_position_ticket=ticket,
        order_id=order.id,
        symbol=symbol,
        direction=Direction.BUY,
        volume=Decimal("0.01"),
        open_price=2650.0,
        stop_loss=2647.5,
        take_profit=2652.5,
        mode=mode,
        state=state,
        opened_at=T0 - timedelta(hours=ticket),
        updated_at=T0 - timedelta(hours=ticket),
    )
    session.add(position)
    session.flush()
    return position


def seed_trade(session: Session, position: PositionRow, pnl: Decimal, closed_at: datetime) -> None:
    session.add(
        TradeRow(
            position_id=position.id,
            mode=position.mode,
            closed_at=closed_at,
            close_price=2652.0,
            pnl_eur=pnl,
            risk_eur=Decimal("1.00"),
            exit_reason="take_profit",
        )
    )
    session.flush()


# --- /markets ---------------------------------------------------------------


def test_markets_lists_each_configured_market(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(
            CandleRow(
                symbol="XAUUSD",
                timeframe=Timeframe.M15,
                open_time=T0 - timedelta(minutes=15),
                open=2649.0,
                high=2651.0,
                low=2648.0,
                close=2650.0,
                source="mt5",
                ingested_at=T0 - timedelta(minutes=15),
            )
        )
        session.commit()
    handler = markets_handler((("XAUUSD", True), ("BTCUSD", False)), CandleStore(engine))

    answer = run(handler, a_request())

    assert "XAUUSD" in answer and "activé" in answer
    assert "BTCUSD" in answer and "désactivé" in answer


def test_markets_shows_the_age_of_the_last_candle(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(
            CandleRow(
                symbol="XAUUSD",
                timeframe=Timeframe.M15,
                open_time=T0 - timedelta(minutes=15),
                open=2649.0,
                high=2651.0,
                low=2648.0,
                close=2650.0,
                source="mt5",
                ingested_at=T0 - timedelta(minutes=15),
            )
        )
        session.commit()
    handler = markets_handler((("XAUUSD", True),), CandleStore(engine))

    answer = run(handler, a_request())

    assert "2026-10-06 11:45" in answer


def test_markets_reports_a_market_without_candles(engine: Engine) -> None:
    handler = markets_handler((("BTCUSD", True),), CandleStore(engine))

    answer = run(handler, a_request())

    assert "BTCUSD" in answer and "aucune bougie" in answer


# --- /signals ---------------------------------------------------------------


def test_signals_lists_the_most_recent_first(engine: Engine) -> None:
    with Session(engine) as session:
        version = seed_version(session)
        seed_signal(
            session,
            version,
            "witness@1.0.0:XAUUSD:M15:2026-10-06T10:00Z",
            generated_at=T0 - timedelta(hours=2),
        )
        seed_signal(
            session,
            version,
            "witness@1.0.0:XAUUSD:M15:2026-10-06T11:00Z",
            generated_at=T0 - timedelta(hours=1),
        )
        session.commit()

    answer = run(signals_handler(engine), a_request("signals"))

    assert answer.index("11:00") < answer.index("10:00")
    assert "XAUUSD" in answer


def test_signals_says_so_when_there_is_none(engine: Engine) -> None:
    answer = run(signals_handler(engine), a_request("signals"))

    assert "aucun signal" in answer.lower()


# --- /positions -------------------------------------------------------------


def test_positions_lists_only_open_ones(engine: Engine) -> None:
    with Session(engine) as session:
        version = seed_version(session)
        seed_position(session, version, 1, state=PositionState.OPEN)
        seed_position(session, version, 2, state=PositionState.CLOSED)
        session.commit()

    answer = run(positions_handler(engine), a_request("positions"))

    assert "XAUUSD" in answer
    assert answer.count("XAUUSD") == 1


def test_positions_says_so_when_none_is_open(engine: Engine) -> None:
    answer = run(positions_handler(engine), a_request("positions"))

    assert "aucune position" in answer.lower()


# --- /performance -----------------------------------------------------------


def test_performance_sums_the_closed_trades(engine: Engine) -> None:
    with Session(engine) as session:
        version = seed_version(session)
        closed = PositionState.CLOSED
        first = seed_position(session, version, 1, state=closed)
        second = seed_position(session, version, 2, state=closed, mode=TradingMode.PAPER)
        third = seed_position(session, version, 3, state=closed)
        seed_trade(session, first, Decimal("10.00"), T0 - timedelta(hours=3))
        seed_trade(session, second, Decimal("-4.00"), T0 - timedelta(hours=2))
        seed_trade(session, third, Decimal("2.50"), T0 - timedelta(hours=1))
        session.commit()

    answer = run(performance_handler(engine), a_request("performance"))

    assert "3" in answer  # trades
    assert "66.7" in answer  # win rate
    assert "8.50" in answer  # total pnl
    assert "PAPER" in answer  # the losing mode is identified
