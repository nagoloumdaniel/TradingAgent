"""The shared analytics vocabulary (F-021, TASK-041).

One closed-trade record, one performance summary. Pure data: the same numbers come out
of a backtest and of production, because both feed the same functions (C-001).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
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
    #: Maximum adverse excursion, in R: how far the trade went against us before it closed.
    #: `1.0` means it reached the stop, whatever the stop's size in points -- which is what
    #: makes two markets comparable, and what lets an analysis answer "was the stop too
    #: tight?" with a number instead of an experiment.
    mae_r: float | None = None
    #: Maximum favourable excursion, in R: how far it went for us, hit or not.
    #:
    #: Both are only known once the trade is closed. Unlike an indicator, nobody decides
    #: with them, so reading them afterwards cannot leak the future into a decision.
    mfe_r: float | None = None
    #: The market context read off the entry bar, as numbers: regime, volatility, session.
    #:
    #: This is what lets an analysis correlate a *context* with an outcome -- "this rule wins
    #: in a trend and loses in a range" -- instead of only correlating parameters. A key that
    #: is absent was not measured and must not be read as zero; see
    #: :func:`tradingagent.indicators.features.entry_features`.
    features: Mapping[str, float] = field(default_factory=dict)


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
