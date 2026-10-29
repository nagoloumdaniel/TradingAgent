"""Single conversion point from the broker server's clock to UTC (risk R-16)."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

_HALF_HOUR = 1800
# A tick is a point in the past by nature: a quiet market serves one a few minutes old, and
# that says nothing about the offset. Rejecting it at two minutes made the check fail every
# evening at 21:07 UTC, when the probe goes quiet (ages of 121-139 s, 2026-10-11 onwards).
# Five minutes stays far from the 900 s where rounding to the half hour turns ambiguous.
MAX_TICK_AGE_SECONDS = 300
# The other direction is stricter: a tick cannot be newer than now, so a tick "from the
# future" means the local clock is behind. Only the seconds of an ordinary skew are allowed.
MAX_TICK_AHEAD_SECONDS = 30


class StaleTickError(Exception):
    """The reference tick is too old to measure the server offset."""


@dataclass(frozen=True)
class ServerClock:
    offset: timedelta

    def to_utc(self, server_epoch: int) -> datetime:
        # MT5 writes the server's wall clock as if it were a UTC epoch.
        return datetime.fromtimestamp(server_epoch, tz=UTC) - self.offset


def measure_offset(tick_server_epoch: int, now_utc: datetime) -> timedelta:
    """Offset of the server clock, read on a recent tick, rounded to the half hour.

    The residue after the rounding is the tick's age (negative drift) or the local clock's
    lag (positive drift). Both are bounded, asymmetrically, and a tick outside the bounds
    measures nothing: the caller must stay prudent rather than guess.
    """
    raw = tick_server_epoch - int(now_utc.timestamp())
    rounded = round(raw / _HALF_HOUR) * _HALF_HOUR
    drift = raw - rounded
    if drift > MAX_TICK_AHEAD_SECONDS:
        raise StaleTickError(
            f"reference tick is {drift} s ahead of the local clock "
            f"(limit {MAX_TICK_AHEAD_SECONDS} s)"
        )
    if -drift > MAX_TICK_AGE_SECONDS:
        raise StaleTickError(f"reference tick is {-drift} s old (limit {MAX_TICK_AGE_SECONDS} s)")
    return timedelta(seconds=rounded)
