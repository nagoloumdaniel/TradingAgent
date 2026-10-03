import math
from collections.abc import Sequence

from tradingagent.indicators._checks import require_finite, require_period, wilder


def rsi(closes: Sequence[float], period: int) -> list[float | None]:
    """Relative Strength Index with Wilder smoothing; first value at index `period`."""
    require_period(period)
    require_finite(closes)
    result: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return result
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    avg_gain = math.fsum(max(change, 0.0) for change in changes[:period]) / period
    avg_loss = math.fsum(max(-change, 0.0) for change in changes[:period]) / period
    result[period] = _rsi_value(avg_gain, avg_loss)
    for index in range(period + 1, len(closes)):
        change = changes[index - 1]
        avg_gain = wilder(avg_gain, max(change, 0.0), period)
        avg_loss = wilder(avg_loss, max(-change, 0.0), period)
        result[index] = _rsi_value(avg_gain, avg_loss)
    return result


def _rsi_value(avg_gain: float, avg_loss: float) -> float | None:
    if avg_loss == 0:
        # A flat window has no defined RSI. Reporting 50 would fabricate a neutral reading.
        return None if avg_gain == 0 else 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)
