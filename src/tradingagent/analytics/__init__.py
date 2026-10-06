"""The shared analytics package (F-021, TASK-041): one code path for production,
reporting and backtest (C-001)."""

from tradingagent.analytics.axes import Axis, group
from tradingagent.analytics.model import MIN_SIGNIFICANT_SAMPLE, Performance, Trade
from tradingagent.analytics.performance import compute_performance

__all__ = [
    "MIN_SIGNIFICANT_SAMPLE",
    "Axis",
    "Performance",
    "Trade",
    "compute_performance",
    "group",
]
