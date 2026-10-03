from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.market_calendar import SlotStatus, learn_calendar

# A Monday at 00:00 UTC, eight full weeks before NOW.
START = datetime(2026, 8, 10, tzinfo=UTC)
NOW = START + timedelta(weeks=8)
FRIDAY, SATURDAY, SUNDAY = 4, 5, 6


def h1(open_time: datetime) -> Candle:
    return Candle(Timeframe.H1, open_time, 100.0, 101.0, 99.0, 100.5)


def history(is_open: Callable[[datetime], bool], start: datetime = START) -> list[Candle]:
    hours = int((NOW - start).total_seconds() // 3600)
    moments = (start + timedelta(hours=i) for i in range(hours))
    return [h1(moment) for moment in moments if is_open(moment)]


def gold_like(moment: datetime) -> bool:
    """Closed Friday 21:00 to Sunday 22:00 UTC, plus a 21:00 break on weekdays."""
    weekday, hour = moment.weekday(), moment.hour
    if (
        weekday == SATURDAY
        or (weekday == FRIDAY and hour >= 21)
        or (weekday == SUNDAY and hour < 22)
    ):
        return False
    return hour != 21


def test_a_market_trading_around_the_clock_is_always_open() -> None:
    calendar = learn_calendar("BTCUSD", history(lambda _: True), NOW)
    assert calendar.always_open
    assert calendar.status_at(datetime(2026, 10, 3, 3, 30, tzinfo=UTC)) is SlotStatus.OPEN


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 10, 2, 20, 30, tzinfo=UTC), SlotStatus.OPEN),  # Friday before the close
        (datetime(2026, 10, 2, 21, 30, tzinfo=UTC), SlotStatus.CLOSED),  # Friday after the close
        (datetime(2026, 10, 3, 12, 0, tzinfo=UTC), SlotStatus.CLOSED),  # Saturday
        (datetime(2026, 10, 4, 21, 0, tzinfo=UTC), SlotStatus.CLOSED),  # Sunday before reopening
        (datetime(2026, 10, 4, 22, 15, tzinfo=UTC), SlotStatus.OPEN),  # Sunday reopening
        (datetime(2026, 9, 30, 21, 10, tzinfo=UTC), SlotStatus.CLOSED),  # daily break
        (datetime(2026, 9, 30, 22, 10, tzinfo=UTC), SlotStatus.OPEN),
    ],
)
def test_weekend_and_daily_break_are_learned(moment: datetime, expected: SlotStatus) -> None:
    calendar = learn_calendar("XAUUSD", history(gold_like), NOW)
    assert not calendar.always_open
    assert calendar.status_at(moment) is expected


def test_a_one_off_holiday_does_not_close_the_slot() -> None:
    holiday = datetime(2026, 8, 31, 14, tzinfo=UTC)  # a Monday 14:00, missing once
    calendar = learn_calendar("XAUUSD", history(lambda m: gold_like(m) and m != holiday), NOW)
    assert calendar.status_at(datetime(2026, 9, 28, 14, 30, tzinfo=UTC)) is SlotStatus.OPEN


def test_a_slot_open_only_some_weeks_is_uncertain_not_closed() -> None:
    # Daylight saving moved the Sunday reopening: 22:00 open for only half of the weeks.
    switch = START + timedelta(weeks=4)

    def shifting(moment: datetime) -> bool:
        if moment.weekday() == SUNDAY and moment.hour == 22:
            return moment >= switch
        return gold_like(moment)

    calendar = learn_calendar("XAUUSD", history(shifting), NOW)
    assert calendar.status_at(datetime(2026, 10, 4, 22, 30, tzinfo=UTC)) is SlotStatus.UNCERTAIN


def test_only_the_recent_weeks_count() -> None:
    old_regime_end = START + timedelta(weeks=12)

    def regime(moment: datetime) -> bool:
        # The market used to trade Saturdays; it stopped twelve weeks after START.
        return True if moment < old_regime_end else gold_like(moment)

    long_now = START + timedelta(weeks=24)
    hours = int((long_now - START).total_seconds() // 3600)
    candles = [
        h1(START + timedelta(hours=i)) for i in range(hours) if regime(START + timedelta(hours=i))
    ]
    calendar = learn_calendar("XAUUSD", candles, long_now, weeks=8)
    assert calendar.status_at(datetime(2027, 1, 23, 12, tzinfo=UTC)) is SlotStatus.CLOSED


def test_no_recent_history_is_refused() -> None:
    with pytest.raises(ValueError, match="history"):
        learn_calendar("XAUUSD", [], NOW)


def test_non_hourly_candles_are_refused() -> None:
    candle = Candle(Timeframe.M15, START, 1.0, 1.0, 1.0, 1.0)
    with pytest.raises(ValueError, match="H1"):
        learn_calendar("XAUUSD", [candle], NOW)


def test_naive_moment_is_refused() -> None:
    calendar = learn_calendar("BTCUSD", history(lambda _: True), NOW)
    with pytest.raises(ValueError, match="UTC"):
        calendar.status_at(datetime(2026, 10, 3, 12))  # noqa: DTZ001 - the point of the test
