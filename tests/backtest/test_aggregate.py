"""F-025: M1 bars aggregate into clock-aligned M5 buckets, holes preserved.

Every expected figure below is a hand computation on a series starting at 10:00 UTC, a
multiple of five minutes from the epoch. Test 2 deliberately starts at 10:03, where a
"bucket = first bar then +5 minutes" implementation and a clock-aligned one disagree, so
the two cannot both pass. Test 3 leaves a ten-minute hole that a filling implementation
would silently paper over with an invented bar.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tradingagent.backtest.aggregate import aggregate
from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe

START = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)


def at(minute: int) -> datetime:
    """A minute of the hour on the reference day, from 10:00 UTC: `at(15)` is 10:15."""
    return datetime(2026, 10, 5, 10, minute, tzinfo=UTC)


def m1(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    """One explicit M1 bar, `index` minutes after START."""
    return Candle(
        timeframe=Timeframe.M1,
        open_time=START + timedelta(minutes=index),
        open=open_,
        high=high,
        low=low,
        close=close,
    )


def walk(index: int, unit: Timeframe = Timeframe.M1) -> Candle:
    """A coherent bar `index` units after START: open = 100 + index, close leads by 0.5."""
    opened = 100.0 + index
    closed = opened + 0.5
    return Candle(
        timeframe=unit,
        open_time=START + timedelta(seconds=unit.seconds * index),
        open=opened,
        high=closed + 0.5,
        low=opened - 0.5,
        close=closed,
    )


def test_fifteen_m1_bars_become_three_m5_bars_with_hand_checked_ohlc() -> None:
    candles = [
        m1(0, 100.0, 101.5, 99.5, 101.0),
        m1(1, 101.0, 103.0, 100.0, 102.5),
        m1(2, 102.5, 103.5, 98.5, 99.0),
        m1(3, 99.0, 101.0, 98.8, 100.0),
        m1(4, 100.0, 106.0, 100.0, 105.5),
        *[walk(index) for index in range(5, 15)],
    ]

    result = aggregate(candles, Timeframe.M5)

    assert len(result) == 3
    assert [bar.open_time for bar in result] == [at(0), at(5), at(10)]
    assert all(bar.timeframe is Timeframe.M5 for bar in result)

    # 10:00 bucket, hand-checked: open 100.0 (bar 0), high 106.0 (bar 4),
    # low 98.5 (bar 2), close 105.5 (bar 4).
    first = result[0]
    assert (first.open, first.high, first.low, first.close) == (100.0, 106.0, 98.5, 105.5)
    assert first.close_time == at(5)
    assert result[-1].close == walk(14).close


def test_buckets_align_on_the_utc_clock_not_on_the_first_bar() -> None:
    """10:03 to 10:12: two bars in 10:00, five in 10:05, three in 10:10, no 10:03 bucket."""
    candles = [walk(index) for index in range(3, 13)]

    result = aggregate(candles, Timeframe.M5)

    assert [bar.open_time for bar in result] == [at(0), at(5), at(10)]
    assert result[0].open == walk(3).open
    assert result[0].close == walk(4).close
    assert result[1].open == walk(5).open
    assert result[1].close == walk(9).close
    assert result[2].open == walk(10).open
    assert result[2].close == walk(12).close


def test_a_ten_minute_hole_invents_no_bar_and_later_buckets_stay_aligned() -> None:
    """10:00, 10:01, then nothing until 10:12: the 10:05 bucket must not exist at all."""
    candles = [walk(0), walk(1), *[walk(index) for index in range(12, 16)]]

    result = aggregate(candles, Timeframe.M5)

    assert [bar.open_time for bar in result] == [at(0), at(10), at(15)]
    # A filler implementation would return four buckets here, including an invented 10:05.
    assert len(result) == 3
    assert result[0].high == walk(1).high
    assert result[0].close == walk(1).close
    assert result[1].open == walk(12).open
    assert result[1].high == walk(14).high
    assert result[1].close == walk(14).close
    assert result[2].close == walk(15).close


def test_a_trailing_partial_bucket_is_kept() -> None:
    candles = [walk(index) for index in range(7)]

    result = aggregate(candles, Timeframe.M5)

    assert len(result) == 2
    tail = result[-1]
    assert tail.open_time == at(5)
    assert tail.close_time == at(10)
    assert (tail.open, tail.high, tail.low, tail.close) == (
        walk(5).open,
        walk(6).high,
        walk(5).low,
        walk(6).close,
    )


def test_unordered_bars_are_refused() -> None:
    with pytest.raises(ValueError, match="ordered"):
        aggregate([walk(1), walk(0)], Timeframe.M5)
    with pytest.raises(ValueError, match="ordered"):
        aggregate([walk(0), walk(0)], Timeframe.M5)


def test_a_target_that_is_not_coarser_is_refused() -> None:
    with pytest.raises(ValueError, match="coarser"):
        aggregate([walk(0)], Timeframe.M1)
    with pytest.raises(ValueError, match="coarser"):
        aggregate([walk(0, Timeframe.M5), walk(1, Timeframe.M5)], Timeframe.M1)


def test_an_empty_series_is_refused() -> None:
    with pytest.raises(ValueError, match="empty"):
        aggregate((), Timeframe.M5)


def test_mixed_source_units_are_refused() -> None:
    with pytest.raises(ValueError, match="mixes"):
        aggregate([walk(0), walk(1, Timeframe.M5)], Timeframe.M5)


def test_two_calls_on_the_same_input_return_the_same_result() -> None:
    candles = [walk(index) for index in range(11)]

    first = aggregate(candles, Timeframe.M5)
    second = aggregate(candles, Timeframe.M5)

    assert isinstance(first, tuple)
    assert first == second
    assert aggregate(tuple(candles), Timeframe.M5) == first
    assert candles == [walk(index) for index in range(11)]
