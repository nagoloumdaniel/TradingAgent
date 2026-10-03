from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import ClassVar

from pydantic import BaseModel

from tradingagent.core.market import Candle
from tradingagent.core.signal import SignalCandidate
from tradingagent.core.timeframe import Timeframe


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Closed candles only, exactly `history_bars` per declared timeframe, oldest first.

    `evaluated_at` is the close time of the triggering candle, never the wall clock.
    """

    symbol: str
    evaluated_at: datetime
    primary_timeframe: Timeframe
    candles: Mapping[Timeframe, tuple[Candle, ...]]

    def __post_init__(self) -> None:
        if self.primary_timeframe not in self.candles:
            raise ValueError(f"primary timeframe {self.primary_timeframe} has no series")
        object.__setattr__(self, "candles", MappingProxyType(dict(self.candles)))

    def series(self, timeframe: Timeframe) -> tuple[Candle, ...]:
        try:
            return self.candles[timeframe]
        except KeyError:
            raise KeyError(
                f"timeframe {timeframe} is not declared by this strategy's manifest"
            ) from None

    def opens(self, timeframe: Timeframe) -> list[float]:
        return [candle.open for candle in self.series(timeframe)]

    def highs(self, timeframe: Timeframe) -> list[float]:
        return [candle.high for candle in self.series(timeframe)]

    def lows(self, timeframe: Timeframe) -> list[float]:
        return [candle.low for candle in self.series(timeframe)]

    def closes(self, timeframe: Timeframe) -> list[float]:
        return [candle.close for candle in self.series(timeframe)]


class Strategy[P: BaseModel](ABC):
    """Stateless decision rule. Same context in, same decision out, in any order."""

    strategy_id: ClassVar[str]
    parameters_model: ClassVar[type[BaseModel]]

    def __init__(self, parameters: P) -> None:
        self._parameters = parameters

    @property
    def parameters(self) -> P:
        return self._parameters

    @abstractmethod
    def evaluate(self, context: StrategyContext) -> SignalCandidate | None: ...
