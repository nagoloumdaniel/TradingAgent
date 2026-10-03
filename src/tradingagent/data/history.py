"""History download and catch-up after an outage (F-004, R-03, TASK-013).

At start-up, and after any reconnection, the agent asks the broker for enough closed bars to
cover both the strategies' warm-up and the time since the last stored bar. The store drops
what it already has, so overlapping requests are safe. An outage longer than the broker
serves leaves a hole that `missing` reports instead of hiding.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.market_calendar import MarketCalendar
from tradingagent.data.quality import missing_bars
from tradingagent.storage.candles import CandleStore

log = logging.getLogger(__name__)


class CandleSource(Protocol):
    async def closed_candles(
        self, symbol: str, timeframe: Timeframe, count: int
    ) -> list[Candle]: ...


@dataclass(frozen=True)
class SyncResult:
    requested: int
    received: int
    inserted: int


def _utc_now() -> datetime:
    return datetime.now(UTC)


class HistorySync:
    def __init__(
        self,
        source: CandleSource,
        store: CandleStore,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._source = source
        self._store = store
        self._now = now

    async def sync(self, symbol: str, timeframe: Timeframe, warmup_bars: int) -> SyncResult:
        """Download what the strategies need plus everything since the last stored bar."""
        now = self._now()
        requested = warmup_bars
        last = self._store.last_open_time(symbol, timeframe)
        if last is not None:
            # One bar of overlap with the stored history proves the junction.
            since_last = (now - last) // timedelta(seconds=timeframe.seconds)
            requested = max(warmup_bars, since_last + 1)
        candles = await self._source.closed_candles(symbol, timeframe, requested)
        inserted = self._store.save(symbol, candles, now)
        log.info(
            "%s %s history: %d requested, %d received, %d new",
            symbol,
            timeframe,
            requested,
            len(candles),
            inserted,
        )
        return SyncResult(requested, len(candles), inserted)

    def missing(
        self,
        symbol: str,
        timeframe: Timeframe,
        calendar: MarketCalendar,
        start: datetime,
        end: datetime,
    ) -> list[datetime]:
        """Stored-history holes in [start, end) during open hours, forming bar excluded."""
        step = timedelta(seconds=timeframe.seconds)
        forming = datetime.fromtimestamp(
            int(self._now().timestamp()) // int(step.total_seconds()) * int(step.total_seconds()),
            tz=UTC,
        )
        end = min(end, forming)
        stored = self._store.between(symbol, timeframe, start, end)
        return missing_bars(stored, timeframe, calendar, start, end)
