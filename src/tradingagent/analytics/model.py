"""The shared analytics vocabulary (F-021, TASK-041).

One closed-trade record, one performance summary. Pure data: the same numbers come out
of a backtest and of production, because both feed the same functions (C-001).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe


@dataclass(frozen=True)
class Trade:
    """One closed operation, whatever produced it (executor, paper or backtest)."""

    symbol: str
    strategy_ref: str  # id@version, e.g. witness@1.0.0
    direction: Direction
    timeframe: Timeframe
    mode: TradingMode
    opened_at: datetime
    closed_at: datetime
    pnl_eur: Decimal
    risk_eur: Decimal
    slippage: float | None = None
    spread: float | None = None


# Below this size, ratio statistics are labelled insignificant instead of being shown.
MIN_SIGNIFICANT_SAMPLE = 30


@dataclass(frozen=True)
class Performance:
    """The section-14.2 indicators for one set of trades."""

    trades: int
    wins: int
    losses: int
    win_rate: Decimal | None
    gross_profit: Decimal
    gross_loss: Decimal
    net_profit: Decimal
    profit_factor: float | None
    expectancy: float | None
    average_win: float | None
    average_loss: float | None
    best: Decimal | None
    worst: Decimal | None
    max_drawdown: Decimal
    max_drawdown_duration: timedelta
    max_win_streak: int
    max_loss_streak: int
    realized_rr: float | None
    average_position_duration: timedelta | None
    average_slippage: float | None
    average_spread: float | None
    sharpe: float | None
    sortino: float | None
    insufficient_sample: bool
