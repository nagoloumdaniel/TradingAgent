"""What each market is doing right now, from the candles the agent already stores.

The reading reuses the strategies' own regime indicators (`trend_of`, `volatility_of`) on the M15
series, so the calendar speaks the same language as the signals: when the page says "tendance
haussière", it is the same trend the strategies see. No candles (feed down, fresh database) is an
*unknown* state, never a neutral one made up to fill the gap.
"""

import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe
from tradingagent.indicators.regime import trend_of, volatility_of
from tradingagent.news.model import MARKETS, MarketState
from tradingagent.resilience import one_line

log = logging.getLogger(__name__)

TIMEFRAME = Timeframe.M15
CANDLE_COUNT = 160
FAST = 10
SLOW = 30
ATR_PERIOD = 14
#: Candles older than this are not "right now": the state is unknown rather than stale.
MAX_AGE = timedelta(hours=6)
#: Enough bars for the slow average and the ATR to mean something.
MIN_CANDLES = SLOW + ATR_PERIOD


class CandleSource(Protocol):
    def latest(
        self, symbol: str, timeframe: Timeframe, count: int, closed_by: datetime | None = None
    ) -> list[Candle]: ...


def state_from_candles(symbol: str, candles: Sequence[Candle], now: datetime) -> MarketState:
    """Pure: the market state a series of candles describes, or an unknown state."""
    if now.utcoffset() is None:
        raise ValueError(f"naive datetime refused, timezone required: {now!r}")
    if len(candles) < MIN_CANDLES:
        return MarketState(symbol)
    last = candles[-1]
    if now - last.close_time > MAX_AGE:
        return MarketState(symbol)
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]
    trend = trend_of(highs, lows, closes, fast=FAST, slow=SLOW, atr_period=ATR_PERIOD)
    volatility = volatility_of(highs, lows, closes, atr_period=ATR_PERIOD)
    return MarketState(
        symbol=symbol,
        trend=trend.value,
        volatility=volatility.value,
        last_price=last.close,
        as_of=last.close_time,
    )


class MarketStateReader:
    """Reads the stored candles of both markets. Never raises: a failing store is unknown state."""

    def __init__(self, candles: CandleSource) -> None:
        self._candles = candles

    def read(self, now: datetime) -> dict[str, MarketState]:
        states: dict[str, MarketState] = {}
        for symbol in MARKETS:
            try:
                candles = self._candles.latest(symbol, TIMEFRAME, CANDLE_COUNT, closed_by=now)
            except Exception as error:  # a broken store must not take the news refresh down
                log.warning("news: no candles for %s: %s", symbol, one_line(error))
                states[symbol] = MarketState(symbol)
                continue
            states[symbol] = state_from_candles(symbol, candles, now)
        return states

    async def read_async(self, now: datetime) -> dict[str, MarketState]:
        return await asyncio.to_thread(self.read, now)
