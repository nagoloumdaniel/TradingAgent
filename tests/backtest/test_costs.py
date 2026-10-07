"""TASK-062 / EF-026: costs are applied adversarially and compare with and without them.

Hand check: with spread 0.4 (half 0.2) and fixed slippage 0.1, a buy reference of 100.0
fills at 100.3. A sell reference of 100.0 fills at 99.7.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tradingagent.analytics.model import Trade
from tradingagent.backtest.costs import CostModel, compare_costs, observed_spread
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe

START = datetime(2026, 10, 3, tzinfo=UTC)


def trade(pnl: float, minutes: int) -> Trade:
    return Trade(
        symbol="frxXAUUSD",
        strategy_ref="witness@1.0.0",
        direction=Direction.BUY,
        timeframe=Timeframe.M15,
        mode=TradingMode.SIGNAL,
        opened_at=START,
        closed_at=START.replace(minute=minutes),
        pnl_eur=Decimal(str(pnl)),
        risk_eur=Decimal("10"),
    )


def test_buy_fills_above_and_sell_fills_below_the_reference() -> None:
    costs = CostModel(spread=0.4, slippage_fixed=0.1)
    assert costs.fill_price(100.0, Direction.BUY, None) == pytest.approx(100.3)
    assert costs.fill_price(100.0, Direction.SELL, None) == pytest.approx(99.7)


def test_slippage_scales_with_volatility_and_multiplier() -> None:
    costs = CostModel(slippage_atr_fraction=0.05, multiplier=2.0)
    assert costs.slippage(2.0) == pytest.approx(0.2)
    assert costs.fill_price(100.0, Direction.BUY, 2.0) == pytest.approx(100.2)


def test_stress_multiplies_every_cost_without_mutating_the_model() -> None:
    base = CostModel(spread=0.4, slippage_fixed=0.1, commission_per_trade=Decimal("1"))
    stressed = base.stressed(3.0)
    assert base.multiplier == 1.0
    assert stressed.multiplier == 3.0
    assert stressed.fill_price(100.0, Direction.BUY, None) == pytest.approx(100.9)
    assert stressed.commission() == Decimal("3.0")


def test_negative_or_incoherent_costs_are_refused() -> None:
    with pytest.raises(ValueError):
        CostModel(spread=-0.1)
    with pytest.raises(ValueError):
        CostModel(execution_delay_bars=-1)
    with pytest.raises(ValueError):
        CostModel(multiplier=0)


def test_observed_spread_is_the_median_of_what_the_terminal_showed() -> None:
    assert observed_spread([0.30, 0.40, 0.50, 0.20]) == pytest.approx(0.35)
    with pytest.raises(ValueError):
        observed_spread([])


def test_comparison_reports_gross_and_net_side_by_side() -> None:
    gross = [trade(10, 1), trade(-4, 2), trade(6, 3)]
    net = [trade(8, 1), trade(-6, 2), trade(4, 3)]
    comparison = compare_costs(gross, net)
    assert comparison.gross.net_profit == Decimal("12")
    assert comparison.net.net_profit == Decimal("6")
    assert comparison.net_profit_delta == Decimal("-6")
    assert comparison.costs_degrade_result is True
    assert len(comparison.trades_with_costs) == 3


def test_a_cost_stress_degrades_the_result_coherently() -> None:
    without = [trade(10, 1), trade(10, 2)]
    with_costs = [trade(9, 1), trade(9, 2)]
    stressed = [trade(7, 1), trade(7, 2)]
    base = compare_costs(without, with_costs)
    hard = compare_costs(without, stressed)
    assert hard.net_profit_delta <= base.net_profit_delta < Decimal(0)
