import math
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from types import MappingProxyType

from tradingagent.core.market import Direction


class InvalidSignalError(ValueError):
    """A strategy produced levels that cannot describe a tradable signal."""


@dataclass(frozen=True, slots=True)
class SignalCandidate:
    """A strategy's decision only. Identity, versions, prices observed and times are added
    by the engine, so a strategy can neither impersonate another nor date its own signal."""

    direction: Direction
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profits: tuple[float, ...]
    reason: str
    indicators: Mapping[str, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "indicators", MappingProxyType(dict(self.indicators)))
        object.__setattr__(self, "take_profits", tuple(self.take_profits))
        levels = (self.entry_low, self.entry_high, self.stop_loss, *self.take_profits)
        if not all(math.isfinite(level) for level in levels):
            raise InvalidSignalError(f"levels must be finite, got {levels}")
        if not all(math.isfinite(value) for value in self.indicators.values()):
            raise InvalidSignalError("indicator values must be finite")
        if not self.reason.strip():
            raise InvalidSignalError("reason must not be blank")
        if not self.take_profits:
            raise InvalidSignalError("at least one take-profit is required")
        if self.entry_low > self.entry_high:
            raise InvalidSignalError("entry_low must not exceed entry_high")
        if self.direction is Direction.BUY:
            self._check_buy()
        else:
            self._check_sell()

    def _check_buy(self) -> None:
        if not self.stop_loss < self.entry_low:
            raise InvalidSignalError("buy stop-loss must be below the entry zone")
        targets = (self.entry_high, *self.take_profits)
        if any(lower >= upper for lower, upper in pairwise(targets)):
            raise InvalidSignalError(
                "buy take-profits must be above the entry zone and strictly increasing"
            )

    def _check_sell(self) -> None:
        if not self.stop_loss > self.entry_high:
            raise InvalidSignalError("sell stop-loss must be above the entry zone")
        targets = (self.entry_low, *self.take_profits)
        if any(upper <= lower for upper, lower in pairwise(targets)):
            raise InvalidSignalError(
                "sell take-profits must be below the entry zone and strictly decreasing"
            )
