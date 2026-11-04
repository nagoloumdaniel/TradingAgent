from datetime import UTC, datetime, timedelta, timezone

import pytest

from tradingagent.data.weekend import WeekendWindow

WINDOW = WeekendWindow()


def utc(day: int, hour: int, minute: int = 0) -> datetime:
    # 2026-11-02 is a Monday
    return datetime(2026, 11, 2 + day, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize(
    ("moment", "closed"),
    [
        (utc(0, 12), False),
        (utc(4, 20, 59), False),
        (utc(4, 21), True),
        (utc(5, 3), True),
        (utc(5, 23, 59), True),
        (utc(6, 21, 59), True),
        (utc(6, 22), False),
        (utc(6, 23), False),
    ],
)
def test_window_boundaries(moment: datetime, closed: bool) -> None:
    assert WINDOW.contains(moment) is closed


def test_a_pc_left_on_overnight_is_closed_on_saturday_morning() -> None:
    assert WINDOW.contains(utc(5, 8))


def test_non_utc_moment_is_rejected() -> None:
    with pytest.raises(ValueError):
        WINDOW.contains(datetime(2026, 11, 7, 8, tzinfo=timezone(timedelta(hours=1))))


def test_one_key_per_weekend() -> None:
    assert WINDOW.week_key(utc(4, 22)) == WINDOW.week_key(utc(5, 9)) == WINDOW.week_key(utc(6, 10))
    assert WINDOW.week_key(utc(4, 22)) != WINDOW.week_key(utc(11, 22))
