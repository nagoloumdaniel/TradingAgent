"""The shared analytics package (F-021, TASK-041): one code path for production,
reporting and backtest (C-001)."""

from tradingagent.analytics.axes import Axis, group
from tradingagent.analytics.model import MIN_SIGNIFICANT_SAMPLE, Performance, Trade
from tradingagent.analytics.performance import compute_performance
from tradingagent.analytics.scalping import (
    Bucket,
    CostSummary,
    Session,
    by_duration,
    by_hour,
    by_session,
    by_size,
    by_spread,
    by_volatility,
    by_weekday,
    cost_summary,
    session_of,
)

__all__ = [
    "MIN_SIGNIFICANT_SAMPLE",
    "Axis",
    "Bucket",
    "CostSummary",
    "Performance",
    "Session",
    "Trade",
    "by_duration",
    "by_hour",
    "by_session",
    "by_size",
    "by_spread",
    "by_volatility",
    "by_weekday",
    "compute_performance",
    "cost_summary",
    "group",
    "session_of",
]
