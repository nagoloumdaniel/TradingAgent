import math
from collections.abc import Sequence

from tradingagent.indicators._checks import require_finite, require_period


def sma(values: Sequence[float], period: int) -> list[float | None]:
    require_period(period)
    require_finite(values)
    result: list[float | None] = [None] * len(values)
    # Exact sum per window instead of a running sum: the value of a bar must not depend on
    # where the series starts, or backtest and production would disagree.
    for index in range(period - 1, len(values)):
        result[index] = math.fsum(values[index - period + 1 : index + 1]) / period
    return result


def ema(values: Sequence[float], period: int) -> list[float | None]:
    require_period(period)
    require_finite(values)
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    alpha = 2 / (period + 1)
    current = math.fsum(values[:period]) / period
    result[period - 1] = current
    for index in range(period, len(values)):
        current = alpha * values[index] + (1 - alpha) * current
        result[index] = current
    return result
