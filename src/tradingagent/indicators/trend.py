"""Supertrend: an ATR-banded trailing line whose direction flips on a band break.

Classic Supertrend bands, with the project ATR (Wilder) reused as is:

    basic_upper[i] = (high[i] + low[i]) / 2 + multiplier * atr[i]
    basic_lower[i] = (high[i] + low[i]) / 2 - multiplier * atr[i]

Band locking: the final upper band only descends and the final lower band only rises,
until the previous close breaks the lock (the band that trails the trend never gives
ground):

    final_upper[i] = basic_upper[i] if basic_upper[i] < final_upper[i-1]
                                      or close[i-1] > final_upper[i-1]
                     else final_upper[i-1]
    final_lower[i] = basic_lower[i] if basic_lower[i] > final_lower[i-1]
                                      or close[i-1] < final_lower[i-1]
                     else final_lower[i-1]

The break that releases a lock is exactly the event that defines the direction, read one
bar earlier to stay causal (nothing at bar i looks at bar i's close):

    trend[i] = True  if close[i-1] > final_upper[i-1]   (break up)
               False if close[i-1] < final_lower[i-1]   (break down)
               trend[i-1] otherwise                     (persistence)

The value of the indicator is the locked band that trails the trend: final_lower while
bullish, final_upper while bearish. Before the first break there is no direction, so no
line either: both stay None rather than inventing a side.
"""

from collections.abc import Sequence

from tradingagent.indicators._checks import require_finite, require_period
from tradingagent.indicators.volatility import atr


def supertrend(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int,
    multiplier: float,
) -> tuple[list[float | None], list[bool | None]]:
    """Return `(line, trend)` aligned with the bars: index `i` describes bar `i`.

    `trend[i]` is True when the trend is bullish, False when bearish, None while no break
    has defined it. `line[i]` is the trailing band of that trend (the lower band while
    bullish, the upper band while bearish) and None while the trend is undefined. No trend
    can appear before index `period + 1`: bar `period` is the first bar with an ATR, and
    breaking its bands takes one more bar. A quiet market can stay undefined much longer,
    and the line stays None for as long as the trend does.
    """
    require_period(period)
    if not len(highs) == len(lows) == len(closes):
        raise ValueError("highs, lows and closes must have the same length")
    for series in (highs, lows, closes):
        require_finite(series)

    lines: list[float | None] = [None] * len(closes)
    trends: list[bool | None] = [None] * len(closes)
    averages = atr(highs, lows, closes, period)
    if len(closes) <= period:
        return lines, trends

    # ATR is defined from index `period` on. Drop the leading Nones so the locking loop
    # deals in floats only: `smoothed[offset]` is the ATR of bar `period + offset`.
    smoothed = [value for value in averages[period:] if value is not None]
    final_upper, final_lower = _basic_bands(highs[period], lows[period], multiplier, smoothed[0])
    trend: bool | None = None
    for offset, average in enumerate(smoothed[1:], start=1):
        index = period + offset
        previous_close = closes[index - 1]
        broke_up = previous_close > final_upper
        broke_down = previous_close < final_lower
        basic_upper, basic_lower = _basic_bands(highs[index], lows[index], multiplier, average)
        if broke_up or basic_upper < final_upper:
            final_upper = basic_upper
        if broke_down or basic_lower > final_lower:
            final_lower = basic_lower
        if broke_up:
            trend = True
        elif broke_down:
            trend = False
        if trend is not None:
            trends[index] = trend
            lines[index] = final_lower if trend else final_upper
    return lines, trends


def _basic_bands(high: float, low: float, multiplier: float, average: float) -> tuple[float, float]:
    middle = (high + low) / 2
    half_range = multiplier * average
    return middle + half_range, middle - half_range
