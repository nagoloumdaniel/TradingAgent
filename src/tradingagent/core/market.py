import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from tradingagent.core.timeframe import Timeframe


class Direction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class Candle:
    timeframe: Timeframe
    open_time: datetime
    open: float
    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        if self.open_time.utcoffset() != timedelta(0):
            raise ValueError(f"open_time must be UTC, got {self.open_time!r}")
        prices = (self.open, self.high, self.low, self.close)
        if not all(math.isfinite(price) for price in prices):
            raise ValueError(f"prices must be finite, got {prices}")
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError(f"incoherent range: open, high, low, close = {prices}")

    @property
    def close_time(self) -> datetime:
        return self.open_time + timedelta(seconds=self.timeframe.seconds)
