"""Losing streaks, as pure functions of the closed trades and an injected moment.

Three views of the same history: the plain streak (the hour-based cooldown), the streak
ending now restricted to the current UTC day (a display figure), and the longest run of
losses reached at any point of the current UTC day (the operator's "three losses in a day,
stop for the day" rule, which is a lock: a later win does not lift it). None of them reads
the clock, the network or the database.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


def utc_day_start(at: datetime) -> datetime:
    return at.replace(hour=0, minute=0, second=0, microsecond=0)


def losing_streak(
    trades_newest_first: Iterable[tuple[Decimal, datetime]], *, since: datetime | None = None
) -> tuple[int, datetime | None]:
    """Consecutive losing trades ending at the latest closed trade.

    Returns the count and the close time of the most recent loss (None when the streak is
    empty). A trade at or above break-even ends the streak, as a win does. With `since`,
    only trades closed at or after that instant are considered: anything older is
    ignored, so a loss closed yesterday never counts toward today's streak.
    """
    streak = 0
    last_loss: datetime | None = None
    for pnl, closed_at in trades_newest_first:
        if since is not None and closed_at < since:
            break
        if pnl >= 0:
            break
        streak += 1
        if last_loss is None:
            last_loss = closed_at
    return streak, last_loss


def losses_today(
    trades_newest_first: Iterable[tuple[Decimal, datetime]], now: datetime
) -> tuple[int, datetime | None]:
    """The losing streak limited to trades closed on the UTC day of `now`."""
    return losing_streak(trades_newest_first, since=utc_day_start(now))


@dataclass(frozen=True)
class DayLossRun:
    """The longest run of consecutive losses reached during one UTC day."""

    longest: int
    day: date


def longest_loss_run_today(trades: Iterable[tuple[Decimal, datetime]], now: datetime) -> DayLossRun:
    """The longest run of consecutive losing trades closed on the UTC day of `now`.

    The trades are walked chronologically, in any input order. A loss is a pnl below zero;
    a win or a break-even trade ends the run. Only trades closed from 00:00 UTC of the day
    of `now` count, so yesterday's run never locks today and the lock lifts at midnight.
    A win closed after the run (a position opened earlier) does not undo it.
    """
    start = utc_day_start(now)
    longest = run = 0
    for pnl, _closed_at in sorted(
        (trade for trade in trades if trade[1] >= start), key=lambda trade: trade[1]
    ):
        run = run + 1 if pnl < 0 else 0
        longest = max(longest, run)
    return DayLossRun(longest, now.date())
