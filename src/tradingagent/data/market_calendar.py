"""Trading hours learned from the broker's own history (F-005, TASK-014).

The MetaTrader5 package does not expose trading sessions (measured in TASK-003), so a
calendar is learned from recent M15 bars and re-learned daily. Quarter-hour slots matter:
gold stops quoting at 20:45 UTC on Fridays, inside an hour that otherwise trades. A slot
open in some weeks only, such as one shifted by daylight saving time, is uncertain.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe

Slot = tuple[int, int]  # (weekday with Monday = 0, quarter of the day 0-95), in UTC
SLOT = timedelta(minutes=15)
SLOTS_PER_WEEK = 7 * 96
# A closure is a run of consecutive closed slots. The walk back is bounded so a corrupt
# calendar cannot make it unbounded; only a closure longer than this would re-announce itself.
CLOSURE_LOOKBACK = timedelta(days=14)


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
        return len(self.open_slots) == SLOTS_PER_WEEK

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
    quarters: Sequence[Candle],
    now: datetime,
    weeks: int = 8,
    open_ratio: float = 0.75,
) -> MarketCalendar:
    """A slot is open when bars exist in at least `open_ratio` of its occurrences."""
    if any(candle.timeframe is not Timeframe.M15 for candle in quarters):
        raise ValueError("the calendar is learned from M15 candles only")
    end = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
    start = end - timedelta(weeks=weeks)
    present: Counter[Slot] = Counter(
        _slot(candle.open_time) for candle in quarters if start <= candle.open_time < end
    )
    if not present:
        raise ValueError(f"no {symbol} history in the last {weeks} weeks to learn hours from")
    occurrences: Counter[Slot] = Counter(
        _slot(start + SLOT * index) for index in range(int((end - start) / SLOT))
    )
    open_slots = {slot for slot, seen in present.items() if seen >= open_ratio * occurrences[slot]}
    uncertain_slots = set(present) - open_slots
    return MarketCalendar(symbol, frozenset(open_slots), frozenset(uncertain_slots))


def _slot(moment: datetime) -> Slot:
    utc = moment.astimezone(UTC)
    return utc.weekday(), (utc.hour * 60 + utc.minute) // 15


def slot_start(moment: datetime) -> datetime:
    """The quarter-hour slot containing `moment`, floored to its first second."""
    return moment.replace(minute=moment.minute - moment.minute % 15, second=0, microsecond=0)


def is_open(calendar: MarketCalendar | None, moment: datetime) -> bool:
    """Whether the calendar proves the symbol trades at `moment` (UTC).

    Pure and fail-closed: the moment is an argument, never read from a clock, and a market
    whose calendar is unknown is not traded. This is what makes a Saturday testable without
    waiting for one.
    """
    if calendar is None:
        return False
    return calendar.status_at(moment) is SlotStatus.OPEN


def closure_started_at(
    calendar: MarketCalendar, moment: datetime, lookback: timedelta = CLOSURE_LOOKBACK
) -> datetime | None:
    """The slot where the closure containing `moment` began, or None if the market is open.

    This is the identity of a closure: every cycle of the same weekend returns the same
    start, which is what lets the operator be told once instead of every twenty seconds. An
    uncertain slot counts as closed, the same fail-closed reading the risk engine uses.
    """
    if calendar.status_at(moment) is SlotStatus.OPEN:
        return None
    start = slot_start(moment)
    limit = start - lookback
    while start - SLOT >= limit and calendar.status_at(start - SLOT) is not SlotStatus.OPEN:
        start -= SLOT
    return start


# Closures shorter than this are normal trading hours, not an incident. Gold breaks every
# day around 21:00 UTC, and announcing that twice a day is saturation rather than
# information — the operator asked to be told about the weekend, not about the lunch break.
MIN_ANNOUNCED_CLOSURE = timedelta(hours=6)


def closure_ends_at(
    calendar: MarketCalendar | None, moment: datetime, *, horizon_days: int = 14
) -> datetime | None:
    """When the market next trades, or None when it does not within the horizon.

    Pure, like the rest: the moment is an argument. Used to tell a weekend apart from a daily
    break — the two are the same thing to `is_open`, and very different things to a person.
    """
    if calendar is None:
        return None
    probe = moment
    limit = moment + timedelta(days=horizon_days)
    while probe <= limit:
        if is_open(calendar, probe):
            return probe
        probe += SLOT
    return None


def closure_lasts_at_least(
    calendar: MarketCalendar | None,
    moment: datetime,
    minimum: timedelta = MIN_ANNOUNCED_CLOSURE,
) -> bool:
    """Whether the closure containing `moment` is long enough to be worth announcing.

    An unknown reopening counts as long: telling the operator about a closure we cannot
    measure is better than leaving them to discover it.
    """
    reopens = closure_ends_at(calendar, moment)
    if reopens is None:
        return True
    return reopens - moment >= minimum
