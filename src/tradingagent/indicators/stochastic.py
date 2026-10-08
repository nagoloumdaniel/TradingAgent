"""Stochastic oscillator: where the close sits inside its own recent range.

The reading is a position, not a trend: 0 means the close is the lowest price of the
window, 100 the highest. A scalping rule uses the two edges as timing gates -- enter a
buy only once the price has come back *up* out of the low zone -- which is why the bounds
matter more here than the middle.

`%K` is the raw reading, `%D` its smoothed average; both are aligned on the input series,
with `None` wherever the window does not exist yet. A flat window has no position at all
and returns `None` rather than a fabricated neutral 50.
"""

from collections.abc import Sequence

from tradingagent.indicators._checks import require_finite, require_period


def stochastic_k(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int
) -> list[float | None]:
    """Raw `%K`; the first defined reading is at index `period - 1`."""
    require_period(period)
    _require_aligned(highs, lows, closes)
    for series in (highs, lows, closes):
        require_finite(series)
    result: list[float | None] = [None] * len(closes)
    for index in range(period - 1, len(closes)):
        window_high = max(highs[index - period + 1 : index + 1])
        window_low = min(lows[index - period + 1 : index + 1])
        span = window_high - window_low
        if span <= 0:
            continue
        result[index] = 100.0 * (closes[index] - window_low) / span
    return result


def stochastic(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int,
    k_smoothing: int,
    d_period: int,
) -> tuple[list[float | None], list[float | None]]:
    """Smoothed `(%K, %D)`, both aligned on the input series.

    The smoothing is a simple mean over the defined readings only: a window that reaches
    back before the first raw value is not a reading, so it stays `None` instead of being
    averaged with a hole.
    """
    raw = stochastic_k(highs, lows, closes, period)
    smoothed = _mean_of_defined(raw, k_smoothing)
    return smoothed, _mean_of_defined(smoothed, d_period)


def _mean_of_defined(values: Sequence[float | None], window: int) -> list[float | None]:
    require_period(window)
    result: list[float | None] = [None] * len(values)
    for index in range(window - 1, len(values)):
        chunk = values[index - window + 1 : index + 1]
        if any(value is None for value in chunk):
            continue
        result[index] = sum(value for value in chunk if value is not None) / window
    return result


def _require_aligned(*series: Sequence[float]) -> None:
    lengths = {len(item) for item in series}
    if len(lengths) > 1:
        raise ValueError(f"highs, lows and closes must have the same length, got {lengths}")
