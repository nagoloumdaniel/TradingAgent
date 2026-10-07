"""Deterministic pseudo-randomness for reproducible research (ENF-008).

The standard library's `random` module is deliberately avoided: its generator is an
implementation detail, and reproducibility is a hard requirement for a replayed backtest
or a Monte-Carlo study. A splitmix64 generator is a few lines, stable across versions, and
fully specified by its seed.
"""

import math
import statistics
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")

_MASK = (1 << 64) - 1
_GOLDEN_GAMMA = 0x9E3779B97F4A7C15


class DeterministicRandom:
    """A seeded splitmix64 stream. Same seed, same sequence, on any platform."""

    __slots__ = ("_spare", "_state")

    def __init__(self, seed: int) -> None:
        self._state = seed & _MASK
        self._spare: float | None = None

    def next_u64(self) -> int:
        self._state = (self._state + _GOLDEN_GAMMA) & _MASK
        value = self._state
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _MASK
        return value ^ (value >> 31)

    def random(self) -> float:
        """A uniform float in [0, 1)."""
        return self.next_u64() / float(1 << 64)

    def uniform(self, low: float, high: float) -> float:
        return low + (high - low) * self.random()

    def gauss(self) -> float:
        """A standard normal deviate (Box-Muller, with the second value kept)."""
        if self._spare is not None:
            spare, self._spare = self._spare, None
            return spare
        # 1 - random() keeps the argument strictly positive.
        radius = math.sqrt(-2.0 * math.log(1.0 - self.random()))
        angle = 2.0 * math.pi * self.random()
        self._spare = radius * math.sin(angle)
        return radius * math.cos(angle)

    def index(self, size: int) -> int:
        if size < 1:
            raise ValueError("cannot draw an index from an empty population")
        return int(self.random() * size)

    def shuffled(self, items: Sequence[T]) -> list[T]:
        shuffled = list(items)
        for position in range(len(shuffled) - 1, 0, -1):
            other = self.index(position + 1)
            shuffled[position], shuffled[other] = shuffled[other], shuffled[position]
        return shuffled


def percentile(sorted_values: Sequence[float], quantile: float) -> float:
    """Linear-interpolation percentile of an already sorted, non-empty sequence."""
    if not sorted_values:
        raise ValueError("cannot take a percentile of an empty sequence")
    if not 0.0 <= quantile <= 1.0:
        raise ValueError(f"quantile must be in [0, 1], got {quantile}")
    position = quantile * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    weight = position - lower
    return float(sorted_values[lower]) * (1.0 - weight) + float(sorted_values[upper]) * weight


def standard_deviation(values: Sequence[float]) -> float:
    return statistics.pstdev(values) if len(values) > 1 else 0.0
