from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ConfigDict

from tradingagent.core.market import Candle
from tradingagent.core.signal import SignalCandidate
from tradingagent.core.timeframe import Timeframe
from tradingagent.strategies.base import Strategy, StrategyContext
from tradingagent.strategies.registry import REGISTRY, build_registry

START = datetime(2026, 10, 3, tzinfo=UTC)


class FrozenParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    threshold: float = 1.0


class MutableParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threshold: float = 1.0


class TolerantParameters(BaseModel):
    model_config = ConfigDict(frozen=True)
    threshold: float = 1.0


class Quiet(Strategy[FrozenParameters]):
    strategy_id = "quiet"
    parameters_model = FrozenParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        return None


class Other(Quiet):
    strategy_id = "other"


class Duplicate(Quiet):
    pass


class Mutable(Strategy[MutableParameters]):
    strategy_id = "mutable"
    parameters_model = MutableParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        return None


class BadId(Quiet):
    strategy_id = "Bad-Id"


class Tolerant(Strategy[TolerantParameters]):
    strategy_id = "tolerant"
    parameters_model = TolerantParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        return None


def candles(count: int) -> tuple[Candle, ...]:
    return tuple(
        Candle(
            timeframe=Timeframe.M15,
            open_time=START + timedelta(minutes=15 * index),
            open=10.0 + index,
            high=11.0 + index,
            low=9.0 + index,
            close=10.5 + index,
        )
        for index in range(count)
    )


def context() -> StrategyContext:
    return StrategyContext(
        symbol="frxXAUUSD",
        evaluated_at=START + timedelta(minutes=45),
        primary_timeframe=Timeframe.M15,
        candles={Timeframe.M15: candles(3)},
    )


def test_context_requires_the_primary_series() -> None:
    with pytest.raises(ValueError, match="primary"):
        StrategyContext(
            symbol="frxXAUUSD",
            evaluated_at=START,
            primary_timeframe=Timeframe.H1,
            candles={Timeframe.M15: candles(3)},
        )


def test_context_price_accessors_are_ordered_oldest_first() -> None:
    built = context()
    assert built.closes(Timeframe.M15) == [10.5, 11.5, 12.5]
    assert built.opens(Timeframe.M15) == [10.0, 11.0, 12.0]
    assert built.highs(Timeframe.M15) == [11.0, 12.0, 13.0]
    assert built.lows(Timeframe.M15) == [9.0, 10.0, 11.0]


def test_context_rejects_undeclared_timeframe_explicitly() -> None:
    with pytest.raises(KeyError, match="not declared"):
        context().series(Timeframe.H1)


def test_context_candles_cannot_be_replaced() -> None:
    with pytest.raises(TypeError):
        context().candles[Timeframe.H1] = ()  # type: ignore[index]


def test_strategy_exposes_its_parameters() -> None:
    assert Quiet(FrozenParameters(threshold=2.0)).parameters.threshold == 2.0


def test_registry_maps_ids_to_classes() -> None:
    assert build_registry(Quiet, Other) == {"quiet": Quiet, "other": Other}


def test_registry_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        build_registry(Quiet, Duplicate)


def test_registry_rejects_mutable_parameters() -> None:
    with pytest.raises(ValueError, match="frozen"):
        build_registry(Mutable)


def test_registry_rejects_parameters_that_ignore_unknown_keys() -> None:
    # A misspelled parameter would otherwise be dropped silently and the default used.
    with pytest.raises(ValueError, match="unknown keys"):
        build_registry(Tolerant)


def test_registry_rejects_malformed_id() -> None:
    with pytest.raises(ValueError, match="strategy_id"):
        build_registry(BadId)


def test_registry_cannot_be_modified() -> None:
    registry = build_registry(Quiet)
    with pytest.raises(TypeError):
        registry["other"] = Other  # type: ignore[index]


def test_production_registry_lists_only_reviewed_strategies() -> None:
    # trend_breakout is reviewed for mechanics only, and its manifest stays capped at SIGNAL.
    #
    # vwap_pullback is the bitcoin base strategy the operator asked for: VWAP anchored at
    # 00:00 UTC, momentum, pullback entry. Its manifest is capped at SIGNAL too -- being new
    # is not a reason to open DEMO, and it has not passed the nine gates. This assertion is
    # the review step: adding a strategy to the registry without editing it fails here.
    assert set(REGISTRY) == {"witness", "trend_breakout", "vwap_pullback"}
