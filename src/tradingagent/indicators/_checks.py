import math
from collections.abc import Sequence


def require_period(period: int) -> None:
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")


def require_finite(values: Sequence[float]) -> None:
    for index, value in enumerate(values):
        if not math.isfinite(value):
            raise ValueError(f"value at index {index} is not finite: {value}")


def wilder(previous: float, value: float, period: int) -> float:
    return (previous * (period - 1) + value) / period
