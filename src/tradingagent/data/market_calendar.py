"""Trading hours learned from the broker's own history (F-005, TASK-014).

The MetaTrader5 package does not expose trading sessions (measured in TASK-003), so a
calendar is learned from recent hourly bars and re-learned daily. A slot open in some weeks
only, such as a reopening hour shifted by daylight saving time, is reported as uncertain.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe

Slot = tuple[int, int]  # (weekday with Monday = 0, hour), in UTC
HOURS_PER_WEEK = 168


class SlotStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class MarketCalendar:
    symbol: str
    open_slots: frozenset[Slot]
    uncertain_slots: frozenset[Slot]

    @property
    def always_open(self) -> bool:
        return len(self.open_slots) == HOURS_PER_WEEK

    def status_at(self, moment: datetime) -> SlotStatus:
        if moment.utcoffset() != timedelta(0):
            raise ValueError(f"moment must be UTC, got {moment!r}")
        slot = _slot(moment)
        if slot in self.open_slots:
            return SlotStatus.OPEN
        if slot in self.uncertain_slots:
            return SlotStatus.UNCERTAIN
        return SlotStatus.CLOSED


def learn_calendar(
    symbol: str,
    hourly: Sequence[Candle],
    now: datetime,
    weeks: int = 8,
    open_ratio: float = 0.75,
) -> MarketCalendar:
    """A slot is open when bars exist in at least `open_ratio` of its occurrences."""
    if any(candle.timeframe is not Timeframe.H1 for candle in hourly):
        raise ValueError("the calendar is learned from H1 candles only")
    end = now.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(weeks=weeks)
    present: Counter[Slot] = Counter(
        _slot(candle.open_time) for candle in hourly if start <= candle.open_time < end
    )
    if not present:
        raise ValueError(f"no {symbol} history in the last {weeks} weeks to learn hours from")
    occurrences: Counter[Slot] = Counter(
        _slot(start + timedelta(hours=offset))
        for offset in range(int((end - start).total_seconds()) // 3600)
    )
    open_slots = {slot for slot, seen in present.items() if seen >= open_ratio * occurrences[slot]}
    uncertain_slots = set(present) - open_slots
    return MarketCalendar(symbol, frozenset(open_slots), frozenset(uncertain_slots))


def _slot(moment: datetime) -> Slot:
    utc = moment.astimezone(UTC)
    return utc.weekday(), utc.hour
