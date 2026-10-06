"""Aggregation axes of section 14.1 (TASK-041)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tradingagent.analytics import group
from tradingagent.analytics.axes import Axis
from tradingagent.analytics.model import Trade
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)  # a Tuesday


def a_trade(
    *,
    symbol: str = "XAUUSD",
    ref: str = "witness@1.0.0",
    direction: Direction = Direction.BUY,
    timeframe: Timeframe = Timeframe.M15,
    mode: TradingMode = TradingMode.DEMO,
    closed_at: datetime = T0,
) -> Trade:
    return Trade(
        symbol=symbol,
        strategy_ref=ref,
        direction=direction,
        timeframe=timeframe,
        mode=mode,
        opened_at=closed_at - timedelta(minutes=30),
        closed_at=closed_at,
        pnl_eur=Decimal(10),
        risk_eur=Decimal(100),
    )


def test_global_axis_puts_everything_in_one_bucket() -> None:
    trades = [a_trade(), a_trade(symbol="BTCUSD")]

    assert group(trades, Axis.GLOBAL) == {"global": trades}


def test_each_axis_groups_by_its_own_field() -> None:
    trades = [
        a_trade(symbol="XAUUSD"),
        a_trade(symbol="BTCUSD"),
        a_trade(ref="trend@2.0.0"),
        a_trade(direction=Direction.SELL),
        a_trade(timeframe=Timeframe.H1),
        a_trade(mode=TradingMode.PAPER),
    ]

    assert set(group(trades, Axis.MARKET)) == {"XAUUSD", "BTCUSD"}
    assert set(group(trades, Axis.STRATEGY)) == {"witness@1.0.0", "trend@2.0.0"}
    assert set(group(trades, Axis.DIRECTION)) == {"BUY", "SELL"}
    assert set(group(trades, Axis.TIMEFRAME)) == {"M15", "H1"}
    assert set(group(trades, Axis.MODE)) == {"DEMO", "PAPER"}


def test_calendar_axes_come_from_the_closing_time() -> None:
    wednesday = datetime(2026, 10, 7, 13, 30, tzinfo=UTC)
    trades = [a_trade(closed_at=T0), a_trade(closed_at=wednesday)]

    assert set(group(trades, Axis.WEEKDAY)) == {"Tuesday", "Wednesday"}
    assert set(group(trades, Axis.HOUR)) == {"12", "13"}
    assert set(group(trades, Axis.MONTH)) == {"2026-10"}


def test_every_trade_falls_in_exactly_one_bucket_per_axis() -> None:
    trades = [a_trade(), a_trade(symbol="BTCUSD"), a_trade(mode=TradingMode.PAPER)]

    for axis in Axis:
        buckets = group(trades, axis)
        assert sum(len(bucket) for bucket in buckets.values()) == len(trades)
