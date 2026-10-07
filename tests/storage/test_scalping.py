"""The §32 statistics that need the database: signal ATR, position size, execution costs.

Every figure is hand-computed from the rows written below:

  * order A sent at T, filled at T+250 ms; order B sent at T+1 s, filled at T+1.5 s;
    measured latencies 250 ms and 500 ms -> median 375 ms, worst 500 ms, 2 samples;
  * their fills carry slippage "1.5" and "0.5" -> mean 1.0, worst 1.5, 2 samples;
  * a fill without a slippage key adds no sample: the mean stays None instead of becoming 0;
  * the ATR read is the one stored with the signal (`signals.indicators`), keyed by
    (symbol, position opened_at); an unknown trade reads None, never a default;
  * the size read is the position's volume.
"""

import itertools
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tradingagent.analytics.model import Trade
from tradingagent.analytics.scalping import by_size, by_volatility
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import ExecutionEventKind, OrderState, PositionState, SignalState
from tradingagent.core.timeframe import Timeframe
from tradingagent.storage.models import (
    OrderRow,
    PositionRow,
    SignalRow,
    StrategyVersionRow,
    TradeRow,
)
from tradingagent.storage.scalping import execution_costs, size_of, volatility_of
from tradingagent.storage.telemetry import ExecutionEventStore

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
COUNTER = itertools.count(1)


@dataclass(frozen=True)
class Stored:
    signal_id: int
    order_id: int


def store_trade(
    engine: Engine,
    *,
    symbol: str = "XAUUSD",
    opened_at: datetime = NOW,
    atr: float | None = 12.0,
    volume: Decimal = Decimal("0.02"),
) -> Stored:
    """One closed trade with its signal, order and position: the whole chain the storage reads."""
    number = next(COUNTER)
    with Session(engine) as session:
        version = StrategyVersionRow(
            ref=f"witness@{number}.0.0",
            strategy_id="witness",
            version=f"{number}.0.0",
            manifest={"strategy_id": "witness"},
            content_hash=f"{number:064d}",
            first_seen_at=NOW,
        )
        signal = SignalRow(
            idempotency_key=f"sig-{number}",
            strategy_version=version,
            symbol=symbol,
            timeframe=Timeframe.M15,
            direction=Direction.BUY,
            mode=TradingMode.DEMO,
            observed_price=4139.0,
            entry_low=4138.5,
            entry_high=4139.5,
            stop_loss=4126.7,
            take_profits=[4163.6],
            reason="test",
            indicators={} if atr is None else {"atr": atr},
            generated_at=NOW,
            expires_at=NOW + timedelta(minutes=15),
            state=SignalState.CANDIDATE,
        )
        order = OrderRow(
            idempotency_key=f"ord-{number}",
            signal=signal,
            symbol=symbol,
            direction=Direction.BUY,
            volume=volume,
            requested_price=4139.0,
            stop_loss=4126.7,
            take_profit=4163.6,
            mode=TradingMode.DEMO,
            state=OrderState.FILLED,
            created_at=NOW,
            updated_at=NOW,
        )
        position = PositionRow(
            broker_position_ticket=9_000_000 + number,
            order=order,
            symbol=symbol,
            direction=Direction.BUY,
            volume=volume,
            open_price=4139.0,
            stop_loss=4126.7,
            take_profit=4163.6,
            mode=TradingMode.DEMO,
            state=PositionState.CLOSED,
            opened_at=opened_at,
            updated_at=NOW,
        )
        session.add(
            TradeRow(
                position=position,
                mode=TradingMode.DEMO,
                closed_at=opened_at + timedelta(minutes=20),
                close_price=4126.7,
                pnl_eur=Decimal("-21.88"),
                risk_eur=Decimal("21.88"),
                exit_reason="stop_loss",
            )
        )
        session.commit()
        return Stored(signal_id=int(signal.id), order_id=int(order.id))


def a_trade(*, symbol: str = "XAUUSD", opened_at: datetime = NOW) -> Trade:
    return Trade(
        symbol=symbol,
        strategy_ref="witness@1.0.0",
        direction=Direction.BUY,
        timeframe=Timeframe.M15,
        mode=TradingMode.DEMO,
        opened_at=opened_at,
        closed_at=opened_at + timedelta(minutes=20),
        pnl_eur=Decimal("-21.88"),
        risk_eur=Decimal("21.88"),
    )


def test_the_signal_atr_is_the_volatility_reading(engine: Engine) -> None:
    store_trade(engine, atr=8.2)

    assert volatility_of(engine)(a_trade()) == pytest.approx(8.2)


def test_a_signal_without_atr_reads_none(engine: Engine) -> None:
    store_trade(engine, atr=None)

    assert volatility_of(engine)(a_trade()) is None


def test_a_trade_the_database_does_not_know_has_no_volatility(engine: Engine) -> None:
    store_trade(engine, opened_at=NOW, atr=8.2)

    assert volatility_of(engine)(a_trade(opened_at=NOW + timedelta(hours=1))) is None
    assert volatility_of(engine)(a_trade(symbol="BTCUSD")) is None


def test_two_trades_are_told_apart_by_symbol_and_opening_time(engine: Engine) -> None:
    store_trade(engine, symbol="XAUUSD", opened_at=NOW, atr=8.2)
    store_trade(engine, symbol="XAUUSD", opened_at=NOW + timedelta(hours=1), atr=15.5)
    store_trade(engine, symbol="BTCUSD", opened_at=NOW, atr=310.0)
    read = volatility_of(engine)

    assert read(a_trade(symbol="XAUUSD", opened_at=NOW)) == pytest.approx(8.2)
    assert read(a_trade(symbol="XAUUSD", opened_at=NOW + timedelta(hours=1))) == pytest.approx(15.5)
    assert read(a_trade(symbol="BTCUSD", opened_at=NOW)) == pytest.approx(310.0)


def test_the_position_volume_is_the_size_reading(engine: Engine) -> None:
    store_trade(engine, volume=Decimal("0.07"))

    assert size_of(engine)(a_trade()) == Decimal("0.07")
    assert size_of(engine)(a_trade(opened_at=NOW + timedelta(hours=1))) is None


def test_the_storage_readings_feed_the_pure_bands(engine: Engine) -> None:
    store_trade(engine, symbol="XAUUSD", opened_at=NOW, atr=1.5)
    store_trade(engine, symbol="XAUUSD", opened_at=NOW + timedelta(hours=1), atr=9.0)
    trades = [a_trade(opened_at=NOW), a_trade(opened_at=NOW + timedelta(hours=1))]

    volatility_bands = by_volatility(trades, volatility_of(engine), (5.0,))
    size_bands = by_size(trades, size_of(engine), (Decimal("0.05"),))

    assert [bucket.key for bucket in volatility_bands] == ["<5", ">=5"]
    assert [bucket.key for bucket in size_bands] == ["<0.05"]


def test_execution_costs_measure_the_real_hops(engine: Engine) -> None:
    first = store_trade(engine)
    second = store_trade(engine)
    telemetry = ExecutionEventStore(engine)
    telemetry.record(ExecutionEventKind.ORDER_SENT, "XAUUSD", {}, NOW, order_id=first.order_id)
    telemetry.record(
        ExecutionEventKind.FILLED,
        "XAUUSD",
        {"slippage": "1.5"},
        NOW + timedelta(milliseconds=250),
        order_id=first.order_id,
    )
    telemetry.record(
        ExecutionEventKind.ORDER_SENT,
        "XAUUSD",
        {},
        NOW + timedelta(seconds=1),
        order_id=second.order_id,
    )
    telemetry.record(
        ExecutionEventKind.FILLED,
        "XAUUSD",
        {"slippage": "0.5"},
        NOW + timedelta(seconds=1, milliseconds=500),
        order_id=second.order_id,
    )

    costs = execution_costs(engine)

    assert costs.symbol is None
    assert costs.sample == 2
    assert costs.median_latency_ms == pytest.approx(375.0)
    assert costs.worst_latency_ms == pytest.approx(500.0)
    assert costs.slippage_sample == 2
    assert costs.average_slippage == pytest.approx(1.0)
    assert costs.max_slippage == pytest.approx(1.5)


def test_execution_costs_are_none_when_nothing_was_measured(engine: Engine) -> None:
    costs = execution_costs(engine)

    assert costs.sample == 0
    assert costs.median_latency_ms is None
    assert costs.worst_latency_ms is None
    assert costs.slippage_sample == 0
    assert costs.average_slippage is None
    assert costs.max_slippage is None


def test_a_fill_without_a_slippage_reading_stays_out_of_the_mean(engine: Engine) -> None:
    stored = store_trade(engine)
    telemetry = ExecutionEventStore(engine)
    telemetry.record(ExecutionEventKind.ORDER_SENT, "XAUUSD", {}, NOW, order_id=stored.order_id)
    telemetry.record(
        ExecutionEventKind.FILLED,
        "XAUUSD",
        {"slippage": None},
        NOW + timedelta(milliseconds=100),
        order_id=stored.order_id,
    )

    costs = execution_costs(engine)

    assert costs.sample == 1
    assert costs.median_latency_ms == pytest.approx(100.0)
    assert costs.slippage_sample == 0
    assert costs.average_slippage is None
    assert costs.max_slippage is None


def test_execution_costs_can_be_restricted_to_one_symbol(engine: Engine) -> None:
    gold = store_trade(engine, symbol="XAUUSD")
    btc = store_trade(engine, symbol="BTCUSD")
    telemetry = ExecutionEventStore(engine)
    telemetry.record(ExecutionEventKind.ORDER_SENT, "XAUUSD", {}, NOW, order_id=gold.order_id)
    telemetry.record(
        ExecutionEventKind.FILLED,
        "XAUUSD",
        {"slippage": "2.0"},
        NOW + timedelta(milliseconds=100),
        order_id=gold.order_id,
    )
    telemetry.record(ExecutionEventKind.ORDER_SENT, "BTCUSD", {}, NOW, order_id=btc.order_id)
    telemetry.record(
        ExecutionEventKind.FILLED,
        "BTCUSD",
        {"slippage": "0.5"},
        NOW + timedelta(seconds=5),
        order_id=btc.order_id,
    )

    gold_costs = execution_costs(engine, "XAUUSD")
    every_costs = execution_costs(engine)

    assert gold_costs.symbol == "XAUUSD"
    assert gold_costs.sample == 1
    assert gold_costs.median_latency_ms == pytest.approx(100.0)
    assert gold_costs.average_slippage == pytest.approx(2.0)
    assert every_costs.sample == 2
    assert every_costs.worst_latency_ms == pytest.approx(5000.0)
