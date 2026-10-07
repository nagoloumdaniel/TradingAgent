"""Transaction-cost modelling for backtests (F-025, EF-026, TASK-062).

Every cost is applied adversarially: a buy fills above the reference price, a sell below
it. The `multiplier` deliberately scales all of them, so a robustness study can ask `what
if costs were twice as bad?` without touching the harness.
"""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from tradingagent.analytics.model import Performance, Trade
from tradingagent.analytics.performance import compute_performance
from tradingagent.core.market import Direction

DEFAULT_COMMISSION = Decimal("0")


@dataclass(frozen=True)
class CostModel:
    """Spread, slippage, commission and execution delay, all in observable units.

    `spread` is the full bid/ask distance in price units; half of it is charged on each
    side. `slippage_atr_fraction` is a fraction of the ATR at decision time and
    `slippage_fixed` a constant price distance. `multiplier` scales every cost at once.
    """

    spread: float = 0.0
    slippage_atr_fraction: float = 0.0
    slippage_fixed: float = 0.0
    commission_per_trade: Decimal = DEFAULT_COMMISSION
    execution_delay_bars: int = 0
    multiplier: float = 1.0

    def __post_init__(self) -> None:
        if self.spread < 0 or self.slippage_atr_fraction < 0 or self.slippage_fixed < 0:
            raise ValueError("costs must not be negative")
        if self.commission_per_trade < 0:
            raise ValueError("commission must not be negative")
        if self.execution_delay_bars < 0:
            raise ValueError("execution_delay_bars must not be negative")
        if self.multiplier <= 0:
            raise ValueError("multiplier must be positive")

    @property
    def half_spread(self) -> float:
        return self.spread * self.multiplier / 2.0

    def slippage(self, atr: float | None) -> float:
        volatility = 0.0 if atr is None else max(atr, 0.0)
        return self.multiplier * (self.slippage_fixed + self.slippage_atr_fraction * volatility)

    def commission(self) -> Decimal:
        return self.commission_per_trade * Decimal(str(self.multiplier))

    def fill_price(self, reference: float, direction: Direction, atr: float | None) -> float:
        """The price a market order would really get: worse than the observed one."""
        adverse = self.half_spread + self.slippage(atr)
        if direction is Direction.BUY:
            return reference + adverse
        return reference - adverse

    def stressed(self, multiplier: float) -> "CostModel":
        if multiplier <= 0:
            raise ValueError("multiplier must be positive")
        return replace(self, multiplier=self.multiplier * multiplier)

    def describe(self) -> dict[str, float | int | str]:
        return {
            "spread": self.spread,
            "slippage_atr_fraction": self.slippage_atr_fraction,
            "slippage_fixed": self.slippage_fixed,
            "commission_per_trade": str(self.commission_per_trade),
            "execution_delay_bars": self.execution_delay_bars,
            "multiplier": self.multiplier,
        }


@dataclass(frozen=True)
class CostComparison:
    """The same trade set measured with and without costs (EF-026)."""

    gross: Performance
    net: Performance
    net_profit_delta: Decimal
    expectancy_delta: float | None
    trades_with_costs: tuple[Trade, ...]

    @property
    def costs_degrade_result(self) -> bool:
        return self.net_profit_delta <= 0


def compare_costs(without_costs: Sequence[Trade], with_costs: Sequence[Trade]) -> CostComparison:
    gross = compute_performance(list(without_costs))
    net = compute_performance(list(with_costs))
    expectancy_delta: float | None = None
    if gross.expectancy is not None and net.expectancy is not None:
        expectancy_delta = net.expectancy - gross.expectancy
    return CostComparison(
        gross=gross,
        net=net,
        net_profit_delta=net.net_profit - gross.net_profit,
        expectancy_delta=expectancy_delta,
        trades_with_costs=tuple(with_costs),
    )


def observed_spread(samples: Sequence[float]) -> float:
    """The spread actually seen on the terminal, summarised by its median."""
    if not samples:
        raise ValueError("no spread sample to observe")
    if any(sample < 0 for sample in samples):
        raise ValueError("a spread cannot be negative")
    return statistics.median(float(sample) for sample in samples)


def spread_as_fraction_of_price(samples: Sequence[float], prices: Sequence[float]) -> float:
    if len(samples) != len(prices):
        raise ValueError("spreads and prices must be the same length")
    ratios = [sample / price for sample, price in zip(samples, prices, strict=True) if price]
    if not ratios:
        raise ValueError("no usable price to turn a spread into a fraction")
    return statistics.median(ratios)
