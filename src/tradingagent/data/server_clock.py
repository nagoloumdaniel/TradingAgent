"""Single conversion point from the broker server's clock to UTC (risk R-16)."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

_HALF_HOUR = 1800
_MAX_TICK_AGE_SECONDS = 120


class StaleTickError(Exception):
    """The reference tick is too old to measure the server offset."""


@dataclass(frozen=True)
class ServerClock:
    offset: timedelta

    def to_utc(self, server_epoch: int) -> datetime:
        # MT5 writes the server's wall clock as if it were a UTC epoch.
        return datetime.fromtimestamp(server_epoch, tz=UTC) - self.offset


def measure_offset(tick_server_epoch: int, now_utc: datetime) -> timedelta:
    """Offset of the server clock, read on a fresh tick, rounded to the half hour."""
    raw = tick_server_epoch - int(now_utc.timestamp())
    rounded = round(raw / _HALF_HOUR) * _HALF_HOUR
    if abs(raw - rounded) > _MAX_TICK_AGE_SECONDS:
        raise StaleTickError(f"reference tick is {abs(raw - rounded)} s off a half-hour offset")
    return timedelta(seconds=rounded)
