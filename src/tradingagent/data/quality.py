"""Health of a candle series before any decision (F-002, RM-001, RM-002, TASK-012).

A closed market blocks signals without being an anomaly; every other non-healthy status is
a defect worth an alert. Missing bars count as a gap only in hours the calendar marks open.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from itertools import pairwise

from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.market_calendar import MarketCalendar, SlotStatus

DEFAULT_MAX_TICK_AGE = timedelta(minutes=2)


class SeriesStatus(StrEnum):
    HEALTHY = "healthy"
    MARKET_CLOSED = "market_closed"
    EMPTY = "empty"
    STALE = "stale"
    GAP = "gap"
    DUPLICATE = "duplicate"
    UNORDERED = "unordered"
    INVALID = "invalid"

    @property
    def is_anomaly(self) -> bool:
        return self not in {SeriesStatus.HEALTHY, SeriesStatus.MARKET_CLOSED}


@dataclass(frozen=True)
class SeriesHealth:
    status: SeriesStatus
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.status is SeriesStatus.HEALTHY

    @property
    def is_anomaly(self) -> bool:
        return self.status.is_anomaly


def assess_series(
    candles: Sequence[Candle],
    timeframe: Timeframe,
    calendar: MarketCalendar,
    now: datetime,
    last_tick_at: datetime | None,
    max_tick_age: timedelta = DEFAULT_MAX_TICK_AGE,
) -> SeriesHealth:
    if not candles:
        return SeriesHealth(SeriesStatus.EMPTY, "no candle")
    foreign = next((c for c in candles if c.timeframe is not timeframe), None)
    if foreign is not None:
        return SeriesHealth(
            SeriesStatus.INVALID, f"{foreign.timeframe} candle in a {timeframe} series"
        )
    for earlier, later in pairwise(candles):
        if earlier.open_time == later.open_time:
            return SeriesHealth(
                SeriesStatus.DUPLICATE, f"two candles open at {_hm(later.open_time)}"
            )
        if earlier.open_time > later.open_time:
            return SeriesHealth(
                SeriesStatus.UNORDERED, f"{_hm(later.open_time)} after {_hm(earlier.open_time)}"
            )

    market = calendar.status_at(now)
    if market is SlotStatus.CLOSED:
        return SeriesHealth(SeriesStatus.MARKET_CLOSED, f"{calendar.symbol} closed at {_hm(now)}")
    tick_is_fresh = last_tick_at is not None and now - last_tick_at <= max_tick_age
    if not tick_is_fresh:
        if market is SlotStatus.UNCERTAIN:
            return SeriesHealth(SeriesStatus.MARKET_CLOSED, "uncertain hour without a fresh tick")
        return SeriesHealth(SeriesStatus.STALE, "no fresh tick while the market should be open")

    step = timedelta(seconds=timeframe.seconds)
    latest_expected = _floor(now, step) - step
    behind = _first_expected_missing(
        candles[-1].open_time + step, latest_expected + step, step, calendar
    )
    if behind is not None:
        return SeriesHealth(
            SeriesStatus.STALE, f"no candle at {_hm(behind)} while the market was open"
        )
    holes = missing_bars(candles, timeframe, calendar, candles[0].open_time, candles[-1].open_time)
    if holes:
        return SeriesHealth(
            SeriesStatus.GAP,
            f"{len(holes)} candle(s) missing while the market was open, first at {_hm(holes[0])}",
        )
    return SeriesHealth(SeriesStatus.HEALTHY)


def missing_bars(
    candles: Sequence[Candle],
    timeframe: Timeframe,
    calendar: MarketCalendar,
    start: datetime,
    end: datetime,
) -> list[datetime]:
    """Every bar time in [start, end), aligned on the timeframe, that the calendar marks open
    and the series lacks. This is the explicit census of holes TASK-013 asks for."""
    step = timedelta(seconds=timeframe.seconds)
    present = {candle.open_time for candle in candles}
    first = _floor(start, step)
    if first < start:
        first += step
    return [
        at
        for at in _times(first, end, step)
        if at not in present and calendar.status_at(at) is SlotStatus.OPEN
    ]


def _first_expected_missing(
    start: datetime, end: datetime, step: timedelta, calendar: MarketCalendar
) -> datetime | None:
    """First bar time in [start, end) falling in an hour the calendar says was open."""
    return next(
        (at for at in _times(start, end, step) if calendar.status_at(at) is SlotStatus.OPEN), None
    )


def _times(start: datetime, end: datetime, step: timedelta) -> Iterator[datetime]:
    moment = start
    while moment < end:
        yield moment
        moment += step


def _floor(moment: datetime, step: timedelta) -> datetime:
    seconds = int(step.total_seconds())
    return datetime.fromtimestamp(int(moment.timestamp()) // seconds * seconds, tz=moment.tzinfo)


def _hm(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M")
