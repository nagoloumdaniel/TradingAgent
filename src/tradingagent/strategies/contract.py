from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe
from tradingagent.strategies.base import Strategy
from tradingagent.strategies.evaluation import Outcome, evaluate
from tradingagent.strategies.manifest import StrategyManifest


def check_strategy_contract(
    strategy: Strategy[Any],
    manifest: StrategyManifest,
    symbol: str,
    candles: Mapping[Timeframe, Sequence[Candle]],
    evaluation_times: Iterable[datetime],
) -> list[str]:
    """Return every breach of the stateless contract; an empty list means compliant.

    The look-ahead check cannot fail for a strategy, since evaluate() cuts the window:
    it guards evaluate() itself against regressions.
    """
    times = list(evaluation_times)

    def run(at: datetime, series: Mapping[Timeframe, Sequence[Candle]] = candles) -> Outcome:
        return evaluate(strategy, manifest, symbol, series, at)

    forward = {at: run(at) for at in times}
    backward = {at: run(at) for at in reversed(times)}
    problems = [
        f"{at.isoformat()}: decision depends on evaluation order"
        for at in times
        if backward[at] != forward[at]
    ]
    problems += [
        f"{at.isoformat()}: non-deterministic decision for the same input"
        for at in times
        if run(at) != run(at)
    ]
    for at in times:
        past_only = {
            timeframe: [candle for candle in series if candle.close_time <= at]
            for timeframe, series in candles.items()
        }
        if run(at, past_only) != forward[at]:
            problems.append(f"{at.isoformat()}: decision changes when future candles are present")
    return problems
