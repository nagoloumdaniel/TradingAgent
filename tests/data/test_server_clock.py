from datetime import UTC, datetime, timedelta

import pytest

from tradingagent.data.server_clock import ServerClock, StaleTickError, measure_offset

NOON_UTC = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def epoch_of(server_wall_clock: datetime) -> int:
    """MT5 writes the server's wall clock as if it were a UTC epoch."""
    return int(server_wall_clock.replace(tzinfo=UTC).timestamp())


def test_zero_offset_server_epoch_is_utc() -> None:
    assert ServerClock(timedelta(0)).to_utc(epoch_of(NOON_UTC)) == NOON_UTC


def test_server_ahead_of_utc_is_shifted_back() -> None:
    server_wall_clock = datetime(2026, 10, 4, 15, 0)  # noqa: DTZ001 - a broker's local wall clock
    assert ServerClock(timedelta(hours=3)).to_utc(epoch_of(server_wall_clock)) == NOON_UTC


def test_conversion_returns_aware_utc() -> None:
    assert ServerClock(timedelta(0)).to_utc(0).tzinfo is UTC


@pytest.mark.parametrize(
    ("server_ahead_by", "expected"),
    [
        (timedelta(0), timedelta(0)),
        (timedelta(hours=3), timedelta(hours=3)),
        (timedelta(hours=-5), timedelta(hours=-5)),
        (timedelta(hours=5, minutes=30), timedelta(hours=5, minutes=30)),
    ],
)
def test_offset_is_measured_from_a_fresh_tick(
    server_ahead_by: timedelta, expected: timedelta
) -> None:
    tick_epoch = epoch_of((NOON_UTC + server_ahead_by - timedelta(seconds=3)).replace(tzinfo=None))
    assert measure_offset(tick_epoch, NOON_UTC) == expected


def test_stale_tick_cannot_measure_the_offset() -> None:
    old_tick = epoch_of((NOON_UTC - timedelta(minutes=10)).replace(tzinfo=None))
    with pytest.raises(StaleTickError):
        measure_offset(old_tick, NOON_UTC)


@pytest.mark.parametrize("age_seconds", [138, 250])
def test_a_quiet_market_tick_still_measures_the_offset(age_seconds: int) -> None:
    """Ticks 125-139 s old were rejected every evening at 21:07 UTC, when the probe goes quiet.

    A tick is a point in the past by nature: how old it is says nothing about the offset.
    """
    for offset in (timedelta(0), timedelta(hours=3)):
        tick_epoch = epoch_of(
            (NOON_UTC + offset - timedelta(seconds=age_seconds)).replace(tzinfo=None)
        )
        assert measure_offset(tick_epoch, NOON_UTC) == offset


def test_a_tick_from_the_future_is_not_a_measurement() -> None:
    """A tick cannot be newer than now: the local clock is the one that is wrong."""
    future_tick = epoch_of((NOON_UTC + timedelta(seconds=584)).replace(tzinfo=None))
    with pytest.raises(StaleTickError, match="ahead"):
        measure_offset(future_tick, NOON_UTC)


def test_a_tick_a_few_seconds_ahead_is_clock_skew_and_is_tolerated() -> None:
    tick_epoch = epoch_of((NOON_UTC + timedelta(seconds=5)).replace(tzinfo=None))
    assert measure_offset(tick_epoch, NOON_UTC) == timedelta(0)


def test_a_tick_older_than_the_limit_says_how_old_it_is() -> None:
    old_tick = epoch_of((NOON_UTC - timedelta(seconds=584)).replace(tzinfo=None))
    with pytest.raises(StaleTickError, match="584 s old"):
        measure_offset(old_tick, NOON_UTC)
