"""Report windows and catch-up (F-022, C-006, TASK-042).

One generator, one window type: daily, weekly and monthly reports are the same window
arithmetic over different periods. A window is due once it is fully elapsed; a restart
that covered the deadline finds the window unsent and catches up.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Period(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass(frozen=True)
class Window:
    period: Period
    start: datetime
    end: datetime


def window_containing(period: Period, at: datetime) -> Window:
    """The [start, end) window of `period` that contains `at` (UTC calendar)."""
    if period is Period.DAILY:
        start = at.replace(hour=0, minute=0, second=0, microsecond=0)
        return Window(period, start, start + timedelta(days=1))
    if period is Period.WEEKLY:
        start = at.replace(hour=0, minute=0, second=0, microsecond=0)
        start -= timedelta(days=start.isoweekday() - 1)  # weeks start on Monday
        return Window(period, start, start + timedelta(days=7))
    start = at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if at.month == 12:
        next_month = start.replace(year=at.year + 1, month=1)
    else:
        next_month = start.replace(month=at.month + 1)
    return Window(period, start, next_month)


def missed_windows(period: Period, last_window_end: datetime | None, now: datetime) -> list[Window]:
    """The windows that ended and were never reported.

    On a first run (no stored end) only the most recent elapsed window is due: the agent
    never floods the operator with the history it does not have.
    """
    current = window_containing(period, now)
    if current.end > now:
        current = window_containing(period, current.start - timedelta(microseconds=1))
    if last_window_end is None:
        return [current] if current.end <= now else []
    windows: list[Window] = []
    cursor = current
    while cursor.end > last_window_end:
        windows.append(cursor)
        previous_start = cursor.start - timedelta(microseconds=1)
        cursor = window_containing(period, previous_start)
        if cursor.end <= last_window_end:
            break
        if len(windows) > 400:  # safety stop, about a year of daily reports
            break
    return list(reversed(windows))
