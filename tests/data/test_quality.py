from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.market_calendar import MarketCalendar, learn_calendar
from tradingagent.data.quality import SeriesHealth, SeriesStatus, assess_series

FRIDAY, SATURDAY, SUNDAY = 4, 5, 6
LEARNED_AT = datetime(2026, 10, 5, tzinfo=UTC)  # a Monday
STEP = timedelta(minutes=15)


def gold_open(moment: datetime) -> bool:
    weekday, hour = moment.weekday(), moment.hour
    if (
        weekday == SATURDAY
        or (weekday == FRIDAY and (hour, moment.minute) >= (20, 45))
        or (weekday == SUNDAY and hour < 22)
    ):
        return False
    return hour != 21


def calendar(
    is_open: Callable[[datetime], bool] = gold_open, uncertain_hour: tuple[int, int] | None = None
) -> MarketCalendar:
    start = LEARNED_AT - timedelta(weeks=8)
    quarters = (start + STEP * i for i in range(8 * 7 * 96))
    early = start + timedelta(weeks=4)
    candles = [
        Candle(Timeframe.M15, at, 1.0, 1.0, 1.0, 1.0)
        for at in quarters
        if is_open(at) and not (uncertain_hour == (at.weekday(), at.hour) and at < early)
    ]
    return learn_calendar("XAUUSD", candles, LEARNED_AT)


GOLD = calendar()
ALWAYS_OPEN = calendar(lambda _: True)


def m15(open_times: list[datetime]) -> list[Candle]:
    return [Candle(Timeframe.M15, at, 100.0, 101.0, 99.0, 100.5) for at in open_times]


def series_until(
    last_open: datetime, count: int, keep: Callable[[datetime], bool] = lambda _: True
) -> list[Candle]:
    times = [last_open - STEP * (count - 1 - i) for i in range(count)]
    return m15([t for t in times if keep(t)])


def assess(
    candles: list[Candle],
    now: datetime,
    cal: MarketCalendar = GOLD,
    tick_age: timedelta | None = timedelta(seconds=5),
) -> SeriesHealth:
    tick = None if tick_age is None else now - tick_age
    return assess_series(candles, Timeframe.M15, cal, now, tick)


TUESDAY_NOON = datetime(2026, 10, 6, 12, 7, tzinfo=UTC)
LAST_CLOSED_TUESDAY = datetime(2026, 10, 6, 11, 45, tzinfo=UTC)


def test_a_complete_fresh_series_is_healthy() -> None:
    health = assess(series_until(LAST_CLOSED_TUESDAY, 40), TUESDAY_NOON)
    assert health.status is SeriesStatus.HEALTHY
    assert health.usable


def test_an_empty_series_is_not_usable() -> None:
    assert assess([], TUESDAY_NOON).status is SeriesStatus.EMPTY


def test_duplicate_candle_is_detected() -> None:
    candles = series_until(LAST_CLOSED_TUESDAY, 10)
    candles.insert(5, candles[5])
    assert assess(candles, TUESDAY_NOON).status is SeriesStatus.DUPLICATE


def test_unordered_series_is_detected() -> None:
    candles = series_until(LAST_CLOSED_TUESDAY, 10)
    candles[3], candles[4] = candles[4], candles[3]
    assert assess(candles, TUESDAY_NOON).status is SeriesStatus.UNORDERED


def test_candle_of_another_timeframe_is_invalid() -> None:
    candles = [
        *series_until(LAST_CLOSED_TUESDAY, 5),
        Candle(Timeframe.H1, TUESDAY_NOON, 1.0, 1.0, 1.0, 1.0),
    ]
    assert assess(candles, TUESDAY_NOON).status is SeriesStatus.INVALID


def test_missing_bar_while_the_market_was_open_is_a_gap() -> None:
    missing = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
    health = assess(series_until(LAST_CLOSED_TUESDAY, 40, lambda t: t != missing), TUESDAY_NOON)
    assert health.status is SeriesStatus.GAP
    assert "10:00" in health.detail
    assert not health.usable


def test_daily_break_is_not_a_gap() -> None:
    wednesday = datetime(2026, 10, 7, 1, 7, tzinfo=UTC)
    last = datetime(2026, 10, 7, 0, 45, tzinfo=UTC)
    candles = series_until(last, 60, lambda t: gold_open(t))
    assert any(c.open_time.hour == 20 for c in candles)  # the window spans the 21:00 break
    assert assess(candles, wednesday).status is SeriesStatus.HEALTHY


def test_weekend_closure_inside_the_window_is_not_a_gap() -> None:
    monday = datetime(2026, 10, 5, 3, 7, tzinfo=UTC)
    last = datetime(2026, 10, 5, 2, 45, tzinfo=UTC)
    candles = series_until(last, 400, lambda t: gold_open(t))
    assert assess(candles, monday).status is SeriesStatus.HEALTHY


def test_missing_bar_in_an_uncertain_hour_is_tolerated() -> None:
    cal = calendar(uncertain_hour=(1, 10))  # Tuesday 10:00 traded only half of the weeks
    missing = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
    health = assess(
        series_until(LAST_CLOSED_TUESDAY, 40, lambda t: t != missing), TUESDAY_NOON, cal
    )
    assert health.status is SeriesStatus.HEALTHY


def test_series_behind_an_open_market_is_stale() -> None:
    health = assess(series_until(LAST_CLOSED_TUESDAY - STEP * 3, 40), TUESDAY_NOON)
    assert health.status is SeriesStatus.STALE
    assert not health.usable


@pytest.mark.parametrize("tick_age", [timedelta(minutes=10), None])
def test_no_fresh_tick_while_the_market_should_be_open_is_stale(tick_age: timedelta | None) -> None:
    health = assess(series_until(LAST_CLOSED_TUESDAY, 40), TUESDAY_NOON, tick_age=tick_age)
    assert health.status is SeriesStatus.STALE


def test_closed_market_blocks_signals_without_being_an_anomaly() -> None:
    saturday = datetime(2026, 10, 10, 12, 7, tzinfo=UTC)
    last = datetime(2026, 10, 9, 20, 45, tzinfo=UTC)  # Friday's last bar
    health = assess(
        series_until(last, 40, lambda t: gold_open(t)), saturday, tick_age=timedelta(hours=15)
    )
    assert health.status is SeriesStatus.MARKET_CLOSED
    assert not health.usable
    assert not health.is_anomaly


def test_stale_tick_in_an_uncertain_hour_counts_as_closed_not_stale() -> None:
    cal = calendar(uncertain_hour=(1, 12))
    health = assess(
        series_until(LAST_CLOSED_TUESDAY, 40), TUESDAY_NOON, cal, tick_age=timedelta(minutes=30)
    )
    assert health.status is SeriesStatus.MARKET_CLOSED


def test_a_market_open_around_the_clock_has_no_closed_hours() -> None:
    saturday = datetime(2026, 10, 10, 12, 7, tzinfo=UTC)
    health = assess(
        series_until(datetime(2026, 10, 10, 11, 45, tzinfo=UTC), 40), saturday, ALWAYS_OPEN
    )
    assert health.status is SeriesStatus.HEALTHY


@pytest.mark.parametrize(
    ("status", "anomaly"),
    [
        (SeriesStatus.HEALTHY, False),
        (SeriesStatus.MARKET_CLOSED, False),
        (SeriesStatus.EMPTY, True),
        (SeriesStatus.STALE, True),
        (SeriesStatus.GAP, True),
        (SeriesStatus.DUPLICATE, True),
        (SeriesStatus.UNORDERED, True),
        (SeriesStatus.INVALID, True),
    ],
)
def test_only_real_defects_are_anomalies(status: SeriesStatus, anomaly: bool) -> None:
    assert status.is_anomaly is anomaly


def test_friday_quote_stop_at_20_45_is_closed_not_stale() -> None:
    # Measured on Deriv: gold's last Friday tick is at 20:44:59, inside an hour that trades.
    friday = datetime(2026, 10, 9, 20, 52, tzinfo=UTC)
    last = datetime(2026, 10, 9, 20, 30, tzinfo=UTC)
    health = assess(
        series_until(last, 40, gold_open), friday, tick_age=timedelta(minutes=7, seconds=1)
    )
    assert health.status is SeriesStatus.MARKET_CLOSED
    assert not health.is_anomaly
