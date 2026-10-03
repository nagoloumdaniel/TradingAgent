from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from typing import Any

from tradingagent.core.market import Candle
from tradingagent.core.signal import InvalidSignalError, SignalCandidate
from tradingagent.core.timeframe import Timeframe
from tradingagent.strategies.base import Strategy, StrategyContext
from tradingagent.strategies.manifest import StrategyManifest


class OutcomeKind(StrEnum):
    SIGNAL = "SIGNAL"
    NO_SIGNAL = "NO_SIGNAL"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    INVALID_SIGNAL = "INVALID_SIGNAL"
    STRATEGY_EXCEPTION = "STRATEGY_EXCEPTION"

    @property
    def is_strategy_error(self) -> bool:
        return self in {OutcomeKind.INVALID_SIGNAL, OutcomeKind.STRATEGY_EXCEPTION}


@dataclass(frozen=True, slots=True)
class Outcome:
    kind: OutcomeKind
    candidate: SignalCandidate | None = None
    detail: str = ""


def evaluate(
    strategy: Strategy[Any],
    manifest: StrategyManifest,
    symbol: str,
    candles: Mapping[Timeframe, Sequence[Candle]],
    evaluated_at: datetime,
) -> Outcome:
    """The only way to run a strategy, in production and in backtest alike.

    Each series must be sorted by open time; only the retained window is checked.
    """
    if manifest.strategy_id != strategy.strategy_id:
        raise ValueError(
            f"manifest strategy_id {manifest.strategy_id!r} does not match {strategy.strategy_id!r}"
        )
    if symbol not in manifest.allowed_symbols:
        raise ValueError(f"symbol {symbol!r} is not allowed by {manifest.ref}")
    if evaluated_at.utcoffset() != timedelta(0):
        raise ValueError(f"evaluated_at must be UTC, got {evaluated_at!r}")

    windows: dict[Timeframe, tuple[Candle, ...]] = {}
    for timeframe in manifest.timeframes:
        series = candles.get(timeframe, ())
        # Binary search: a backtest may pass the full history on every call.
        cut = bisect_right(series, evaluated_at, key=_close_time)
        if cut < manifest.history_bars:
            return Outcome(
                OutcomeKind.INSUFFICIENT_HISTORY,
                detail=f"{timeframe}: {cut}/{manifest.history_bars} closed candles",
            )
        windows[timeframe] = tuple(series[cut - manifest.history_bars : cut])

    for timeframe, window in windows.items():
        _check_window(timeframe, window)
    if windows[manifest.primary_timeframe][-1].close_time != evaluated_at:
        raise ValueError(
            f"no {manifest.primary_timeframe} candle closes at {evaluated_at.isoformat()}: "
            "evaluate only on receipt of the triggering candle"
        )

    context = StrategyContext(symbol=symbol, evaluated_at=evaluated_at, candles=windows)
    try:
        result = strategy.evaluate(context)
    except InvalidSignalError as error:
        return Outcome(OutcomeKind.INVALID_SIGNAL, detail=str(error))
    except Exception as error:
        # Containing a faulty strategy is the point: one market must not take down the others.
        return Outcome(OutcomeKind.STRATEGY_EXCEPTION, detail=f"{type(error).__name__}: {error}")
    if result is None:
        return Outcome(OutcomeKind.NO_SIGNAL)
    if not isinstance(result, SignalCandidate):
        return Outcome(
            OutcomeKind.INVALID_SIGNAL,
            detail=f"expected SignalCandidate or None, got {type(result).__name__}",
        )
    return Outcome(OutcomeKind.SIGNAL, candidate=result)


def _close_time(candle: Candle) -> datetime:
    return candle.close_time


def _check_window(timeframe: Timeframe, window: tuple[Candle, ...]) -> None:
    for candle in window:
        if candle.timeframe is not timeframe:
            raise ValueError(
                f"wrong timeframe: {candle.timeframe} candle found in the {timeframe} series"
            )
    for earlier, later in pairwise(window):
        if earlier.open_time >= later.open_time:
            raise ValueError(f"{timeframe} series is not sorted by open time")
