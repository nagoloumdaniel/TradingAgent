"""Every indicator is checked against a hand-documented computation (TASK-041).

The reference dataset, closed in this order, pnl in EUR, risk 100 each:

    #   pnl    cumulative   note
    1  +150     150         win
    2   -80      70         loss
    3  +120     190         win  (new equity peak)
    4   -80     110
    5   -80      30         trough of the drawdown opened at #3
    6  +200     230         win  (new peak: the #3 drawdown lasted from #3 to #6)

Hand-computed figures: gross profit 470, gross loss -240, net 230,
profit factor 470/240 = 1.958333..., win rate 0.5, expectancy 230/6 = 38.3333...,
average win 470/3 = 156.6666..., average loss -80, best +200, worst -80,
max drawdown 190-30 = 160, realized R/R 230/600 = 0.38333...,
max win streak 1, max loss streak 2.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradingagent.analytics import compute_performance
from tradingagent.analytics.model import Trade
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
PNLS = [150, -80, 120, -80, -80, 200]


def the_trades() -> list[Trade]:
    trades = []
    for step, pnl in enumerate(PNLS):
        closed = T0 + timedelta(hours=step + 1)
        trades.append(
            Trade(
                symbol="XAUUSD",
                strategy_ref="witness@1.0.0",
                direction=Direction.BUY if pnl > 0 else Direction.SELL,
                timeframe=Timeframe.M15,
                mode=TradingMode.DEMO,
                opened_at=closed - timedelta(minutes=30),
                closed_at=closed,
                pnl_eur=Decimal(pnl),
                risk_eur=Decimal(100),
            )
        )
    return trades


def test_trade_counts_and_rates() -> None:
    performance = compute_performance(the_trades())

    assert performance.trades == 6
    assert performance.wins == 3
    assert performance.losses == 3
    assert performance.win_rate == Decimal("0.5")


def test_profits_and_factor() -> None:
    performance = compute_performance(the_trades())

    assert performance.gross_profit == Decimal(470)
    assert performance.gross_loss == Decimal(-240)
    assert performance.net_profit == Decimal(230)
    assert performance.profit_factor == pytest.approx(1.9583333333333333)
    assert performance.expectancy == pytest.approx(38.3333333333333333)
    assert performance.average_win == pytest.approx(156.6666666666666667)
    assert performance.average_loss == Decimal(-80)
    assert performance.best == Decimal(200)
    assert performance.worst == Decimal(-80)


def test_drawdown_and_streaks() -> None:
    trades = the_trades()
    performance = compute_performance(trades)

    assert performance.max_drawdown == Decimal(160)
    # The drawdown opened at the equity peak of trade #3 and closed at the peak of #6.
    assert performance.max_drawdown_duration == trades[5].closed_at - trades[2].closed_at
    assert performance.max_win_streak == 1
    assert performance.max_loss_streak == 2


def test_realized_return_over_risk_and_average_duration() -> None:
    performance = compute_performance(the_trades())

    assert performance.realized_rr == pytest.approx(0.3833333333333333)
    assert performance.average_position_duration == timedelta(minutes=30)


def test_ratios_below_the_significance_threshold_refuse_to_speak() -> None:
    performance = compute_performance(the_trades())

    assert performance.sharpe is None
    assert performance.sortino is None
    assert performance.insufficient_sample is True


def test_sharpe_and_sortino_once_the_sample_is_significant() -> None:
    # 30 trades, risk 100 each: twenty wins at +100 and ten losses at -100, i.e. twenty
    # r-multiples at +1 and ten at -1. Mean 1/3; sample variance
    # (20*(2/3)^2 + 10*(4/3)^2)/29 = (240/9)/29; sharpe = mean / std ≈ 0.3476109.
    # Downside deviation sqrt((10*1)/29) ≈ 0.5872202; sortino ≈ 0.5676462.
    trades = []
    for index in range(30):
        win = index % 3 != 2  # ten losses among thirty
        closed = T0 + timedelta(hours=index + 1)
        trades.append(
            Trade(
                symbol="XAUUSD",
                strategy_ref="witness@1.0.0",
                direction=Direction.BUY if win else Direction.SELL,
                timeframe=Timeframe.M15,
                mode=TradingMode.DEMO,
                opened_at=closed - timedelta(minutes=30),
                closed_at=closed,
                pnl_eur=Decimal(100 if win else -100),
                risk_eur=Decimal(100),
            )
        )

    performance = compute_performance(trades)

    assert performance.insufficient_sample is False
    assert performance.sharpe == pytest.approx(0.3476108935769035, abs=1e-9)
    assert performance.sortino == pytest.approx(0.5676462121975466, abs=1e-9)


def test_an_empty_book_produces_nones_never_zeros_that_lie() -> None:
    performance = compute_performance([])

    assert performance.trades == 0
    assert performance.win_rate is None
    assert performance.profit_factor is None
    assert performance.expectancy is None
    assert performance.max_drawdown == Decimal(0)


def test_optional_execution_qualities_are_averaged_when_present() -> None:
    trades = [
        Trade(
            symbol="XAUUSD",
            strategy_ref="witness@1.0.0",
            direction=Direction.BUY,
            timeframe=Timeframe.M15,
            mode=TradingMode.DEMO,
            opened_at=T0,
            closed_at=T0 + timedelta(hours=1),
            pnl_eur=Decimal(50),
            risk_eur=Decimal(100),
            slippage=0.2,
            spread=0.5,
        ),
        Trade(
            symbol="XAUUSD",
            strategy_ref="witness@1.0.0",
            direction=Direction.BUY,
            timeframe=Timeframe.M15,
            mode=TradingMode.DEMO,
            opened_at=T0 + timedelta(hours=2),
            closed_at=T0 + timedelta(hours=3),
            pnl_eur=Decimal(-30),
            risk_eur=Decimal(100),
            slippage=0.4,
            spread=0.7,
        ),
    ]

    performance = compute_performance(trades)

    assert performance.average_slippage == pytest.approx(0.3)
    assert performance.average_spread == pytest.approx(0.6)
