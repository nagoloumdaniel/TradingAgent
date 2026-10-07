from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.market_calendar import (
    SlotStatus,
    closure_ends_at,
    closure_lasts_at_least,
    closure_started_at,
    is_open,
    learn_calendar,
)

# A Monday at 00:00 UTC, eight full weeks before NOW.
START = datetime(2026, 8, 10, tzinfo=UTC)
NOW = START + timedelta(weeks=8)
FRIDAY, SATURDAY, SUNDAY = 4, 5, 6

# 2026-10-02 is a Friday, 10-03 a Saturday, 10-04 a Sunday and 10-05 a Monday.
FRIDAY_CLOSE = datetime(2026, 10, 2, 20, 50, tzinfo=UTC)
SATURDAY_NOON = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
SUNDAY_NOON = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
SUNDAY_REOPEN = datetime(2026, 10, 4, 22, 15, tzinfo=UTC)
MONDAY_NOON = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


QUARTER = timedelta(minutes=15)


def m15(open_time: datetime) -> Candle:
    return Candle(Timeframe.M15, open_time, 100.0, 101.0, 99.0, 100.5)


def history(is_open: Callable[[datetime], bool], start: datetime = START) -> list[Candle]:
    quarters = int((NOW - start) / QUARTER)
    moments = (start + QUARTER * i for i in range(quarters))
    return [m15(moment) for moment in moments if is_open(moment)]


def gold_like(moment: datetime) -> bool:
    """Closed Friday 20:45 to Sunday 22:00 UTC, as measured on Deriv, plus a 21:00 daily break."""
    weekday, hour = moment.weekday(), moment.hour
    if (
        weekday == SATURDAY
        or (weekday == FRIDAY and (hour, moment.minute) >= (20, 45))
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
        (datetime(2026, 10, 2, 20, 50, tzinfo=UTC), SlotStatus.CLOSED),  # Friday 20:45 close
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
    # 14:05 falls in the 14:00 quarter, the very slot that missed one week.
    assert calendar.status_at(datetime(2026, 9, 28, 14, 5, tzinfo=UTC)) is SlotStatus.OPEN


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
    quarters = int((long_now - START) / QUARTER)
    candles = [m15(START + QUARTER * i) for i in range(quarters) if regime(START + QUARTER * i)]
    calendar = learn_calendar("XAUUSD", candles, long_now, weeks=8)
    assert calendar.status_at(datetime(2027, 1, 23, 12, tzinfo=UTC)) is SlotStatus.CLOSED


def test_no_recent_history_is_refused() -> None:
    with pytest.raises(ValueError, match="history"):
        learn_calendar("XAUUSD", [], NOW)


def test_candles_other_than_m15_are_refused() -> None:
    candle = Candle(Timeframe.H1, START, 1.0, 1.0, 1.0, 1.0)
    with pytest.raises(ValueError, match="M15"):
        learn_calendar("XAUUSD", [candle], NOW)


def test_naive_moment_is_refused() -> None:
    calendar = learn_calendar("BTCUSD", history(lambda _: True), NOW)
    with pytest.raises(ValueError, match="UTC"):
        calendar.status_at(datetime(2026, 10, 3, 12))  # noqa: DTZ001 - the point of the test


# --- what the weekend means for a market, as a pure predicate ------------------------------
#
# `is_open` and `closure_started_at` take the moment as an argument: no clock is read, so a
# Saturday can be injected and the weekend behaviour is proven, not waited for.


def test_gold_is_not_open_over_the_weekend_and_is_open_on_monday() -> None:
    gold = learn_calendar("XAUUSD", history(gold_like), NOW)

    assert is_open(gold, FRIDAY_CLOSE) is False  # the Friday 20:45 close
    assert is_open(gold, SATURDAY_NOON) is False
    assert is_open(gold, SUNDAY_NOON) is False
    assert is_open(gold, SUNDAY_REOPEN) is True  # the Sunday 22:00 reopening
    assert is_open(gold, MONDAY_NOON) is True


def test_a_seven_day_market_stays_open_on_the_weekend() -> None:
    bitcoin = learn_calendar("BTCUSD", history(lambda _: True), NOW)

    assert is_open(bitcoin, SATURDAY_NOON) is True
    assert is_open(bitcoin, SUNDAY_NOON) is True


def test_without_a_calendar_nothing_is_proven_open() -> None:
    assert is_open(None, MONDAY_NOON) is False


def test_the_weekend_closure_has_one_identity_from_saturday_to_sunday() -> None:
    gold = learn_calendar("XAUUSD", history(gold_like), NOW)
    friday_close = datetime(2026, 10, 2, 20, 45, tzinfo=UTC)

    assert closure_started_at(gold, SATURDAY_NOON) == friday_close
    assert closure_started_at(gold, SUNDAY_NOON) == friday_close
    # Two cycles twenty seconds apart are the same closure, so they can be announced once.
    assert closure_started_at(gold, SATURDAY_NOON + timedelta(seconds=20)) == friday_close


def test_an_open_market_has_no_closure_to_announce() -> None:
    gold = learn_calendar("XAUUSD", history(gold_like), NOW)

    assert closure_started_at(gold, MONDAY_NOON) is None
    assert closure_started_at(gold, SUNDAY_REOPEN) is None


def test_the_daily_break_is_its_own_closure() -> None:
    gold = learn_calendar("XAUUSD", history(gold_like), NOW)

    # The 21:00 UTC break is a second closure, distinct from the weekend one.
    assert closure_started_at(gold, datetime(2026, 9, 30, 21, 10, tzinfo=UTC)) == datetime(
        2026, 9, 30, 21, 0, tzinfo=UTC
    )


# --- which closures deserve a message ------------------------------------------------------
#
# The operator asked to hear about the weekend. Gold also breaks every day around 21:00 UTC,
# and telling him twice a day is saturation — the stop stays, the announcement does not.


def test_the_weekend_is_long_enough_to_announce() -> None:
    gold = learn_calendar("XAUUSD", history(gold_like), NOW)

    assert closure_lasts_at_least(gold, SATURDAY_NOON) is True
    assert closure_lasts_at_least(gold, SUNDAY_NOON) is True


def test_the_daily_break_is_too_short_to_announce() -> None:
    """The market is still stopped; only the message is filtered."""
    gold = learn_calendar("XAUUSD", history(gold_like), NOW)

    assert closure_started_at(gold, datetime(2026, 9, 30, 21, 10, tzinfo=UTC)) is not None
    assert closure_lasts_at_least(gold, datetime(2026, 9, 30, 21, 10, tzinfo=UTC)) is False


def test_a_closure_we_cannot_measure_is_announced() -> None:
    """Leaving the operator to discover a closure by himself is the worse failure."""
    assert closure_lasts_at_least(None, MONDAY_NOON) is True


def test_the_reopening_is_found_by_the_same_calendar() -> None:
    gold = learn_calendar("XAUUSD", history(gold_like), NOW)

    # The Sunday 22:00 UTC session: the first slot that trades again after the weekend.
    assert closure_ends_at(gold, SATURDAY_NOON) == datetime(2026, 10, 4, 22, 0, tzinfo=UTC)
    assert closure_ends_at(gold, MONDAY_NOON) == MONDAY_NOON  # already open


def test_an_always_open_market_has_no_closure_to_measure() -> None:
    bitcoin = learn_calendar("BTCUSD", history(lambda _: True), NOW)

    assert closure_ends_at(bitcoin, SATURDAY_NOON) == SATURDAY_NOON
