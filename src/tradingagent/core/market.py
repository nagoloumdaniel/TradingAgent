import math
from dataclasses import dataclass, field
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
    #: The broker's tick volume, keyword-only so `Candle(*row)` keeps its six arguments.
    #:
    #: `None` means "this series carries no volume", which is the honest reading of every
    #: dataset frozen before 2026-10-08 and of the storage table, which has no such column.
    #: A volume-weighted average needs to tell "no trades" (0) from "not recorded" (None),
    #: so the two are not collapsed into one value.
    volume: float | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.open_time.utcoffset() != timedelta(0):
            raise ValueError(f"open_time must be UTC, got {self.open_time!r}")
        prices = (self.open, self.high, self.low, self.close)
        if not all(math.isfinite(price) for price in prices):
            raise ValueError(f"prices must be finite, got {prices}")
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError(f"incoherent range: open, high, low, close = {prices}")
        if self.volume is not None and (not math.isfinite(self.volume) or self.volume < 0):
            raise ValueError(f"volume must be a finite count >= 0, got {self.volume!r}")

    @property
    def close_time(self) -> datetime:
        return self.open_time + timedelta(seconds=self.timeframe.seconds)
