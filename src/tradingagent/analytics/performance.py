"""The section-14.2 indicators, as pure functions (TASK-041).

Every figure comes from the trade list alone — no clock, no database, no network — so a
backtest and a production window are measured by exactly the same code. Ratios that need
a significant sample (Sharpe, Sortino) return None and raise the `insufficient_sample`
flag below `MIN_SIGNIFICANT_SAMPLE` trades instead of pretending precision.
"""

import math
import statistics
from datetime import datetime, timedelta
from decimal import Decimal

from tradingagent.analytics.model import MIN_SIGNIFICANT_SAMPLE, Performance, Trade


def compute_performance(trades: list[Trade]) -> Performance:
    ordered = sorted(trades, key=lambda trade: (trade.closed_at, trade.opened_at))
    pnls = [trade.pnl_eur for trade in ordered]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]

    gross_profit = sum(wins, Decimal(0))
    gross_loss = sum(losses, Decimal(0))
    net = gross_profit + gross_loss

    cumulative = Decimal(0)
    equity: list[tuple[datetime, Decimal]] = []
    for trade in ordered:
        cumulative += trade.pnl_eur
        equity.append((trade.closed_at, cumulative))

    max_drawdown, max_duration = _drawdown(equity)
    win_streak, loss_streak = _streaks(pnls)
    significant = len(ordered) >= MIN_SIGNIFICANT_SAMPLE
    sharpe, sortino = _risk_adjusted(ordered) if significant else (None, None)

    return Performance(
        trades=len(ordered),
        wins=len(wins),
        losses=len(losses),
        win_rate=Decimal(len(wins)) / Decimal(len(ordered)) if ordered else None,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_profit=net,
        profit_factor=_ratio(gross_profit, abs(gross_loss)),
        expectancy=_ratio(net, Decimal(len(ordered))),
        average_win=_ratio(gross_profit, Decimal(len(wins))),
        average_loss=_ratio(gross_loss, Decimal(len(losses))),
        best=max(pnls) if pnls else None,
        worst=min(pnls) if pnls else None,
        max_drawdown=max_drawdown,
        max_drawdown_duration=max_duration,
        max_win_streak=win_streak,
        max_loss_streak=loss_streak,
        realized_rr=_ratio(net, sum((t.risk_eur for t in ordered), Decimal(0))),
        average_position_duration=(
            sum(
                (t.closed_at - t.opened_at for t in ordered),
                timedelta(0),
            )
            / len(ordered)
        )
        if ordered
        else None,
        average_slippage=_mean_of([t.slippage for t in ordered if t.slippage is not None]),
        average_spread=_mean_of([t.spread for t in ordered if t.spread is not None]),
        sharpe=sharpe,
        sortino=sortino,
        insufficient_sample=not significant,
    )


def _ratio(numerator: Decimal, denominator: Decimal) -> float | None:
    if denominator == 0:
        return None
    return float(numerator / denominator)


def _mean_of(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _drawdown(equity: list[tuple[datetime, Decimal]]) -> tuple[Decimal, timedelta]:
    """Maximum peak-to-trough fall, and how long the longest one took to recover."""
    worst = Decimal(0)
    worst_duration = timedelta(0)
    peak = Decimal(0)
    peak_at: datetime | None = None
    for moment, value in equity:
        # The duration is measured before the peak moves: the recovery point itself
        # closes the interval that started at the previous peak.
        if peak_at is not None:
            duration = moment - peak_at
            if duration > worst_duration:
                worst_duration = duration
        if value >= peak:
            peak, peak_at = value, moment
        else:
            fall = peak - value
            if fall > worst:
                worst = fall
    return worst, worst_duration


def _streaks(pnls: list[Decimal]) -> tuple[int, int]:
    best_win = best_loss = 0
    run_win = run_loss = 0
    for pnl in pnls:
        if pnl > 0:
            run_win += 1
            run_loss = 0
        elif pnl < 0:
            run_loss += 1
            run_win = 0
        else:
            run_win = run_loss = 0
        best_win = max(best_win, run_win)
        best_loss = max(best_loss, run_loss)
    return best_win, best_loss


def _risk_adjusted(trades: list[Trade]) -> tuple[float | None, float | None]:
    """Sharpe and Sortino on the realized return-over-risk multiples (not annualized:
    the operating rhythm is the strategy's own)."""
    multiples = [float(t.pnl_eur / t.risk_eur) for t in trades if t.risk_eur != 0]
    if len(multiples) < 2:
        return None, None
    mean = statistics.fmean(multiples)
    deviation = statistics.stdev(multiples)
    downside = math.sqrt(sum(min(m, 0.0) ** 2 for m in multiples) / (len(multiples) - 1))
    sharpe = mean / deviation if deviation > 0 else None
    sortino = mean / downside if downside > 0 else None
    return sharpe, sortino
