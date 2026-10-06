"""Aggregation axes of section 14.1 (TASK-041)."""

from collections import defaultdict
from collections.abc import Iterable
from enum import StrEnum

from tradingagent.analytics.model import Trade


class Axis(StrEnum):
    """Section 14.1. Market regimes are P3 and deliberately absent."""

    GLOBAL = "global"
    MARKET = "market"
    STRATEGY = "strategy"
    DIRECTION = "direction"
    TIMEFRAME = "timeframe"
    WEEKDAY = "weekday"
    HOUR = "hour"
    MONTH = "month"
    MODE = "mode"


def _key(trade: Trade, axis: Axis) -> str:
    if axis is Axis.GLOBAL:
        return "global"
    if axis is Axis.MARKET:
        return trade.symbol
    if axis is Axis.STRATEGY:
        return trade.strategy_ref
    if axis is Axis.DIRECTION:
        return trade.direction.value
    if axis is Axis.TIMEFRAME:
        return trade.timeframe.value
    if axis is Axis.WEEKDAY:
        return trade.closed_at.strftime("%A")
    if axis is Axis.HOUR:
        return f"{trade.closed_at:%H}"
    if axis is Axis.MONTH:
        return f"{trade.closed_at:%Y-%m}"
    return trade.mode.value


def group(trades: Iterable[Trade], axis: Axis) -> dict[str, list[Trade]]:
    """Split the trades along one axis; every trade lands in exactly one bucket."""
    buckets: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        buckets[_key(trade, axis)].append(trade)
    return dict(buckets)
