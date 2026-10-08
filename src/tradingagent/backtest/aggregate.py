"""Aggregation of a fine candle unit into a coarser one (F-025).

A strategy that decides on M1 still needs a slower filter — an EMA on M5, say — and the
frozen M1 datasets hold nothing else, so the M5 series is built here from the M1 bars it
actually contains. Every coarse bar is therefore a statement about observed data and
nothing more.

Buckets are aligned on the UTC clock rather than on the first bar: a series starting at
10:03 yields a partial 10:00 bucket, then full 10:05 buckets. Clock alignment (a multiple
of the bucket size since the epoch) keeps the coarse series identical whatever moment a
download happened to start at.

A bucket with no source bar does not exist in the output. Gold closes for the weekend and
feeds drop quotes; inventing a bar to bridge a hole would hand a strategy a price the
market never printed, which is precisely what the dataset layer counts and refuses to fill
(`data.quality.missing_bars`).
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import pairwise

from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe


def aggregate(candles: Sequence[Candle], target: Timeframe) -> tuple[Candle, ...]:
    """Regroupe des bougies d'une unité fine en bougies de `target`.

    `open` is the first bar's open, `high` the highest high, `low` the lowest low and
    `close` the last bar's close; `open_time` is the bucket start and `close_time` its end,
    so even a partial bucket reports the bucket it belongs to.

    The input must be strictly ordered and of a single unit, and `target` must be coarser
    than that unit; anything else raises `ValueError`, because a reordered or mixed series
    would quietly produce a plausible coarse bar that is simply wrong.

    A trailing partial bucket is kept: dropping it would hide the freshest bars from a
    strategy that has to decide now.
    """
    if not candles:
        raise ValueError("cannot aggregate an empty candle series")
    source = candles[0].timeframe
    for candle in candles[1:]:
        if candle.timeframe is not source:
            raise ValueError(
                f"input mixes {source.value} and {candle.timeframe.value} candles at "
                f"{candle.open_time}; aggregate one unit at a time"
            )
    if target.seconds <= source.seconds:
        raise ValueError(f"target {target.value} is not coarser than the input unit {source.value}")
    for earlier, later in pairwise(candles):
        if earlier.open_time >= later.open_time:
            raise ValueError(f"candles are not strictly ordered at {later.open_time}")

    aggregated: list[Candle] = []
    members: list[Candle] = []
    current: int | None = None
    for candle in candles:
        bucket = _bucket_seconds(candle.open_time, target)
        if current is not None and bucket != current:
            aggregated.append(_merge(members, target))
            members = []
        current = bucket
        members.append(candle)
    aggregated.append(_merge(members, target))
    return tuple(aggregated)


def _bucket_seconds(moment: datetime, target: Timeframe) -> int:
    """The epoch second the bucket holding `moment` starts at."""
    return int(moment.timestamp()) // target.seconds * target.seconds


def _merge(members: Sequence[Candle], target: Timeframe) -> Candle:
    """Collapse the bars of one bucket; `members` is never empty."""
    first = members[0]
    last = members[-1]
    return Candle(
        timeframe=target,
        open_time=datetime.fromtimestamp(_bucket_seconds(first.open_time, target), tz=UTC),
        open=first.open,
        high=max(candle.high for candle in members),
        low=min(candle.low for candle in members),
        close=last.close,
        volume=_summed_volume(members),
    )


def _summed_volume(members: Sequence[Candle]) -> float | None:
    """The bucket's tick volume, or `None` if any member is missing its own.

    A volume-weighted price built on a partial sum is not an approximation: it is a number
    about a different series, and it would silently disagree with the same bucket built
    from a complete download. `None` says "this bucket does not know", which is the only
    reading a caller can act on. A bucket of zero volumes still sums to 0.0 -- no trading is
    a measurement, not a hole.
    """
    volumes = [candle.volume for candle in members]
    if any(volume is None for volume in volumes):
        return None
    return sum(volume for volume in volumes if volume is not None)
