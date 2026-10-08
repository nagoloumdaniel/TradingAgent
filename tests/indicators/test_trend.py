"""Hand computations for the Supertrend, written before the code under test.

Period 2 is used on purpose: Wilder smoothing (alpha = 1/n = 1/2) stays easy to follow.
Every bar is symmetrical around its close (high = close + 0.5, low = close - 0.5), so the
median band (high + low) / 2 is the close itself and only the ATR moves the bands.
"""

import math
from collections.abc import Sequence

import pytest

from tradingagent.indicators.trend import supertrend

HALF_RANGE = 0.5
PERIOD = 2
MULTIPLIER = 1.0

# Flat warm-up, regular rise, top, then a reversal: the flip indexes are hand computed below.
REVERSAL = [10, 10, 10, 11, 12, 13, 12, 10, 8]

# Flat warm-up, crash, recovery, then a pullback that must not flip the trend back down.
PULLBACK = [10, 10, 10, 6, 4, 8, 12, 14, 16, 15, 17]

# Two long legs, used for the locked-band monotonicity property.
WAVE = [10, 10, 10, 11, 12, 13, 14, 15, 16, 15, 13, 11, 9, 7, 6, 7, 9, 11, 13, 15, 17]


def _bars(closes: Sequence[float]) -> tuple[list[float], list[float], list[float]]:
    highs = [close + HALF_RANGE for close in closes]
    lows = [close - HALF_RANGE for close in closes]
    return highs, lows, list(closes)


def _run(
    closes: Sequence[float], period: int = PERIOD, multiplier: float = MULTIPLIER
) -> tuple[list[float | None], list[bool | None]]:
    highs, lows, series = _bars(closes)
    return supertrend(highs, lows, series, period, multiplier)


def _values(series: Sequence[float | None]) -> list[float]:
    assert all(value is not None for value in series)
    return [value for value in series if value is not None]


@pytest.mark.parametrize("length", [0, 1, 2, 3])
def test_too_short_series_yields_no_value_at_all(length: int) -> None:
    # The first ATR value lands at index `period`; up to there nothing is defined.
    lines, trends = _run(REVERSAL[:length], period=3)
    assert lines == [None] * length
    assert trends == [None] * length


@pytest.mark.parametrize("period", [0, -1])
def test_invalid_period_is_rejected(period: int) -> None:
    with pytest.raises(ValueError, match="period"):
        _run(REVERSAL, period=period)


def test_series_of_different_lengths_are_rejected() -> None:
    with pytest.raises(ValueError, match="same length"):
        supertrend([1.0, 2.0, 3.0], [1.0, 2.0], [1.0, 2.0, 3.0], PERIOD, MULTIPLIER)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_input_is_rejected_instead_of_propagated(bad: float) -> None:
    for position in range(3):
        series = [list(part) for part in _bars(REVERSAL)]
        series[position][4] = bad
        with pytest.raises(ValueError, match="finite"):
            supertrend(series[0], series[1], series[2], PERIOD, MULTIPLIER)


def test_rise_then_reversal_flips_exactly_where_hand_computed() -> None:
    lines, trends = _run(REVERSAL)
    assert len(lines) == len(trends) == len(REVERSAL)
    # closes 10 10 10 11 12 13 12 10 8 ; true ranges 1 1 1.5 1.5 1.5 1.5 2.5 2.5
    # ATR: idx2 (1+1)/2=1 ; idx3 (1+1.5)/2=1.25 ; idx4 (1.25+1.5)/2=1.375 ;
    # idx5 (1.375+1.5)/2=1.4375 ; idx6 (1.4375+1.5)/2=1.46875 ;
    # idx7 (1.46875+2.5)/2=1.984375 ; idx8 (1.984375+2.5)/2=2.2421875
    # idx2: lock starts, basic upper 11, basic lower 9, no break yet -> None
    # idx3: close[2]=10 inside 9..11 -> None
    # idx4: close[3]=11 is not > 11 -> None
    # idx5: close[4]=12 > final upper 11 -> bullish, line = final lower 11.5625
    # idx6: close[5]=13 inside 11.5625..14.4375 -> still bullish, line 11.5625
    # idx7: close[6]=12 >= final lower 11.5625 -> still bullish, line 11.5625
    # idx8: close[7]=10 < final lower 11.5625 -> bearish, line = final upper 10.2421875
    assert trends[:5] == [None] * 5
    assert trends[5:8] == [True, True, True]
    assert trends[8] is False
    assert lines[:5] == [None] * 5
    assert _values(lines[5:8]) == pytest.approx([11.5625, 11.5625, 11.5625])
    assert lines[8] == pytest.approx(10.2421875)


def test_trend_persists_between_flips_instead_of_oscillating() -> None:
    _, trends = _run(PULLBACK)
    # idx2: lock starts, basic upper 11, basic lower 9 -> None
    # idx3: close[2]=10 inside 9..11 -> None
    # idx4: close[3]=6 < final lower 9 -> bearish (line = final upper 6.625)
    # idx5: close[4]=4 inside 1.375..6.625 -> still bearish
    # idx6: close[5]=8 > final upper 6.625 -> bullish (line = final lower 7.96875)
    # idx7..idx10: no break, the 16 -> 15 pullback at idx9 stays above the line -> bullish
    flips = [
        index
        for index in range(1, len(trends))
        if trends[index] is not None and trends[index] != trends[index - 1]
    ]
    assert flips == [4, 6]
    assert trends[4:6] == [False, False]
    assert trends[6:] == [True] * 5
    lines, _ = _run(PULLBACK)
    assert _values(lines[4:6]) == pytest.approx([6.625, 6.625])
    assert _values(lines[6:]) == pytest.approx(
        [7.96875, 10.734375, 13.1171875, 13.1171875, 14.654296875]
    )


def test_locked_band_never_retreats_inside_a_trend() -> None:
    lines, trends = _run(WAVE)
    rises: list[float] = []
    falls: list[float] = []
    for index in range(1, len(lines)):
        if trends[index] is None or trends[index] != trends[index - 1]:
            continue  # a flip (or the first defined bar) has no same-trend predecessor
        previous_line, current_line = lines[index - 1], lines[index]
        assert previous_line is not None
        assert current_line is not None
        if trends[index]:
            # bullish: the returned line is the locked lower band, it can only rise
            assert current_line >= previous_line, f"bullish line retreated at index {index}"
            rises.append(current_line - previous_line)
        else:
            # bearish: the returned line is the locked upper band, it can only fall
            assert current_line <= previous_line, f"bearish line advanced at index {index}"
            falls.append(previous_line - current_line)
    # non-vacuity: both trends were exercised and both locked bands actually moved
    assert max(rises) > 0
    assert max(falls) > 0


def test_same_input_gives_the_same_output_and_is_not_mutated() -> None:
    highs, lows, closes = _bars(WAVE)
    snapshot = (list(highs), list(lows), list(closes))
    first = supertrend(highs, lows, closes, PERIOD, MULTIPLIER)
    second = supertrend(highs, lows, closes, PERIOD, MULTIPLIER)
    assert first == second
    assert supertrend(tuple(highs), tuple(lows), tuple(closes), PERIOD, MULTIPLIER) == first
    assert (highs, lows, closes) == snapshot
