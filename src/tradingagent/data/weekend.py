"""The weekend window during which the whole platform stays switched off.

Gold stops quoting on Friday at 20:45 UTC and reopens on Sunday evening. The window starts
15 minutes after the Friday close and ends once Sunday trading is established, so nothing
runs, restarts or reconnects to the terminal in between. Pure: the moment is an argument.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

FRIDAY = 4
SUNDAY = 6


@dataclass(frozen=True)
class WeekendWindow:
    """From Friday `closes_at` to Sunday `reopens_at`, both in UTC."""

    closes_at: time = time(21, 0)
    reopens_at: time = time(22, 0)

    def contains(self, moment: datetime) -> bool:
        if moment.utcoffset() != timedelta(0):
            raise ValueError(f"moment must be UTC, got {moment!r}")
        utc = moment.astimezone(UTC)
        day, clock = utc.weekday(), utc.time()
        if day == FRIDAY:
            return clock >= self.closes_at
        if day == SUNDAY:
            return clock < self.reopens_at
        return day == FRIDAY + 1

    def reopening(self, moment: datetime) -> datetime:
        """The next Sunday reopening at or after `moment`'s weekend."""
        utc = moment.astimezone(UTC)
        days_ahead = (SUNDAY - utc.weekday()) % 7
        return datetime.combine(utc.date() + timedelta(days=days_ahead), self.reopens_at, UTC)

    def week_key(self, moment: datetime) -> str:
        """Identifies one weekend, so the closing notice is sent once per weekend."""
        reopening = self.reopening(moment)
        return reopening.strftime("%Y-%m-%d")
