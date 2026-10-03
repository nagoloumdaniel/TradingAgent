import math
from collections.abc import Sequence

from tradingagent.indicators._checks import require_finite, require_period, wilder


def atr(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int
) -> list[float | None]:
    """Average True Range with Wilder smoothing; first value at index `period`.

    The first bar has no true range: it needs a previous close, and substituting
    high - low there would be an approximation.
    """
    require_period(period)
    if not len(highs) == len(lows) == len(closes):
        raise ValueError("highs, lows and closes must have the same length")
    for series in (highs, lows, closes):
        require_finite(series)
    result: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return result
    ranges = [
        _true_range(highs[index], lows[index], closes[index - 1]) for index in range(1, len(closes))
    ]
    current = math.fsum(ranges[:period]) / period
    result[period] = current
    for index in range(period + 1, len(closes)):
        current = wilder(current, ranges[index - 1], period)
        result[index] = current
    return result


def _true_range(high: float, low: float, previous_close: float) -> float:
    return max(high - low, abs(high - previous_close), abs(low - previous_close))
