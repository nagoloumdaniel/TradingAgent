"""Reference values are computed by hand in the comments, independently of the code under test.

Periods of 2 are used on purpose: Wilder smoothing (alpha = 1/n) and the standard EMA
(alpha = 2/(n+1)) then differ (1/2 against 2/3), so a test cannot pass with the wrong one.
"""

import math
from collections.abc import Callable, Sequence

import pytest

from tradingagent.indicators.momentum import rsi
from tradingagent.indicators.moving_average import ema, sma
from tradingagent.indicators.volatility import atr

Series = list[float | None]


def assert_series(actual: Series, expected: Series) -> None:
    assert [value is None for value in actual] == [value is None for value in expected]
    assert [value for value in actual if value is not None] == pytest.approx(
        [value for value in expected if value is not None]
    )


def test_sma_matches_hand_computation() -> None:
    # (1+2+3)/3, (2+3+4)/3, (3+4+5)/3
    assert_series(sma([1, 2, 3, 4, 5], 3), [None, None, 2, 3, 4])


def test_ema_is_seeded_with_the_sma_then_smoothed() -> None:
    # alpha = 2/(3+1) = 0.5 ; seed = (2+4+6)/3 = 4 ; 0.5*8+0.5*4 = 6 ; 0.5*4+0.5*6 = 5
    assert_series(ema([2, 4, 6, 8, 4], 3), [None, None, 4, 6, 5])


def test_ema_differs_from_sma_on_the_same_window() -> None:
    assert ema([2, 4, 6, 8, 4], 3)[-1] != sma([2, 4, 6, 8, 4], 3)[-1]


def test_rsi_uses_wilder_smoothing() -> None:
    # changes: +2 -1 +2 0 -4
    # idx2: avg_gain=(2+0)/2=1, avg_loss=(0+1)/2=0.5, RS=2, RSI=100-100/3=200/3
    # idx3: gain=(1+2)/2=1.5, loss=(0.5+0)/2=0.25, RS=6, RSI=100-100/7=600/7
    # idx4: gain=(1.5+0)/2=0.75, loss=(0.25+0)/2=0.125, RS=6, RSI=600/7
    # idx5: gain=(0.75+0)/2=0.375, loss=(0.125+4)/2=2.0625, RS=2/11, RSI=200/13
    assert_series(
        rsi([10, 12, 11, 13, 13, 9], 2),
        [None, None, 200 / 3, 600 / 7, 600 / 7, 200 / 13],
    )


def test_rsi_is_100_when_there_are_only_gains() -> None:
    assert rsi([1, 2, 3], 2)[-1] == 100


def test_rsi_is_0_when_there_are_only_losses() -> None:
    assert rsi([3, 2, 1], 2)[-1] == 0


def test_rsi_of_a_flat_market_is_undefined_not_neutral() -> None:
    assert rsi([5, 5, 5, 5], 2) == [None, None, None, None]


def test_atr_uses_true_range_and_wilder_smoothing() -> None:
    # bar: high low close -> true range
    # 0: 10 8 9    -> undefined, no previous close
    # 1: 11 9 10   -> max(2, |11-9|, |9-9|)    = 2
    # 2: 12 10 11  -> max(2, |12-10|, |10-10|) = 2
    # 3: 16 14 15  -> max(2, |16-11|, |14-11|) = 5   gap up: high vs previous close wins
    # 4: 13 9 9    -> max(4, |13-15|, |9-15|)  = 6   gap down: low vs previous close wins
    # idx2: (2+2)/2 = 2 ; idx3: (2*1+5)/2 = 3.5 ; idx4: (3.5*1+6)/2 = 4.75
    highs = [10, 11, 12, 16, 13]
    lows = [8, 9, 10, 14, 9]
    closes = [9, 10, 11, 15, 9]
    assert_series(atr(highs, lows, closes, 2), [None, None, 2, 3.5, 4.75])


def test_atr_rejects_series_of_different_lengths() -> None:
    with pytest.raises(ValueError, match="same length"):
        atr([1, 2], [1, 2], [1], 1)


def _sma(values: Sequence[float], period: int) -> Series:
    return sma(values, period)


def _ema(values: Sequence[float], period: int) -> Series:
    return ema(values, period)


def _rsi(values: Sequence[float], period: int) -> Series:
    return rsi(values, period)


def _atr(values: Sequence[float], period: int) -> Series:
    return atr([value + 1 for value in values], [value - 1 for value in values], values, period)


INDICATORS: list[tuple[str, Callable[[Sequence[float], int], Series], int]] = [
    # name, indicator, number of leading undefined values for the period
    ("sma", _sma, -1),
    ("ema", _ema, -1),
    ("rsi", _rsi, 0),
    ("atr", _atr, 0),
]
VALUES = [10.0, 11.5, 10.8, 12.2, 13.0, 12.4, 11.9, 12.8, 13.6, 13.1]


@pytest.mark.parametrize(("name", "indicator", "offset"), INDICATORS)
def test_output_is_aligned_with_input(
    name: str, indicator: Callable[[Sequence[float], int], Series], offset: int
) -> None:
    assert len(indicator(VALUES, 3)) == len(VALUES)


@pytest.mark.parametrize(("name", "indicator", "offset"), INDICATORS)
def test_first_value_appears_exactly_when_enough_data(
    name: str, indicator: Callable[[Sequence[float], int], Series], offset: int
) -> None:
    period = 3
    result = indicator(VALUES, period)
    first_defined = period + offset
    assert all(value is None for value in result[:first_defined])
    assert result[first_defined] is not None


@pytest.mark.parametrize(("name", "indicator", "offset"), INDICATORS)
def test_too_short_series_yields_no_value_at_all(
    name: str, indicator: Callable[[Sequence[float], int], Series], offset: int
) -> None:
    short = VALUES[: 3 + offset]
    assert indicator(short, 3) == [None] * len(short)


@pytest.mark.parametrize(("name", "indicator", "offset"), INDICATORS)
def test_empty_series_yields_empty_output(
    name: str, indicator: Callable[[Sequence[float], int], Series], offset: int
) -> None:
    assert indicator([], 3) == []


@pytest.mark.parametrize(("name", "indicator", "offset"), INDICATORS)
@pytest.mark.parametrize("period", [0, -1])
def test_invalid_period_is_rejected(
    name: str, indicator: Callable[[Sequence[float], int], Series], offset: int, period: int
) -> None:
    with pytest.raises(ValueError, match="period"):
        indicator(VALUES, period)


@pytest.mark.parametrize(("name", "indicator", "offset"), INDICATORS)
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_input_is_rejected_instead_of_propagated(
    name: str, indicator: Callable[[Sequence[float], int], Series], offset: int, bad: float
) -> None:
    with pytest.raises(ValueError, match="finite"):
        indicator([*VALUES[:5], bad, *VALUES[5:]], 3)


@pytest.mark.parametrize(("name", "indicator", "offset"), INDICATORS)
def test_input_is_not_mutated(
    name: str, indicator: Callable[[Sequence[float], int], Series], offset: int
) -> None:
    values = list(VALUES)
    indicator(values, 3)
    assert values == VALUES


@pytest.mark.parametrize(("name", "indicator", "offset"), INDICATORS)
def test_same_input_gives_same_output(
    name: str, indicator: Callable[[Sequence[float], int], Series], offset: int
) -> None:
    assert indicator(VALUES, 3) == indicator(tuple(VALUES), 3)
