from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from tradingagent.core.market import Candle, Direction
from tradingagent.core.signal import SignalCandidate
from tradingagent.core.timeframe import Timeframe
from tradingagent.strategies import contract
from tradingagent.strategies.base import Strategy, StrategyContext
from tradingagent.strategies.evaluation import Outcome, OutcomeKind
from tradingagent.strategies.manifest import StrategyManifest

START = datetime(2026, 10, 3, tzinfo=UTC)
GOLD = "frxXAUUSD"
STEP = timedelta(minutes=15)
CLOSES = [10, 11, 9, 12, 13, 11, 14, 10, 15, 16, 12, 17]
CANDLES = {
    Timeframe.M15: [
        Candle(Timeframe.M15, START + STEP * index, close, close + 1, close - 1, close)
        for index, close in enumerate(CLOSES)
    ]
}
TIMES = [START + STEP * (index + 1) for index in range(2, len(CLOSES))]


class NoParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Conforming(Strategy[NoParameters]):
    strategy_id = "conforming"
    parameters_model = NoParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        closes = context.closes(Timeframe.M15)
        if closes[-1] <= max(closes[:-1]):
            return None
        return SignalCandidate(
            direction=Direction.BUY,
            entry_low=closes[-1],
            entry_high=closes[-1],
            stop_loss=min(closes) - 1,
            take_profits=(closes[-1] + 5,),
            reason="new high",
            indicators={},
        )


class Memory(Conforming):
    """Hidden state: buys when the close rose since the previous call, whatever its time."""

    strategy_id = "memory"

    def __init__(self, parameters: NoParameters) -> None:
        super().__init__(parameters)
        self.previous: float | None = None

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        closes = context.closes(Timeframe.M15)
        previous, self.previous = self.previous, closes[-1]
        if previous is None or closes[-1] <= previous:
            return None
        return SignalCandidate(
            direction=Direction.BUY,
            entry_low=closes[-1],
            entry_high=closes[-1],
            stop_loss=min(closes) - 1,
            take_profits=(closes[-1] + 5,),
            reason="rose since last call",
            indicators={},
        )


class Flipping(Conforming):
    """Answers differently on each call for the same input."""

    strategy_id = "flipping"

    def __init__(self, parameters: NoParameters) -> None:
        super().__init__(parameters)
        self.flip = False

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        self.flip = not self.flip
        return super().evaluate(context) if self.flip else None


def manifest(strategy_id: str) -> StrategyManifest:
    return StrategyManifest.model_validate(
        {
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "max_mode": "SIGNAL",
            "allowed_symbols": [GOLD],
            "timeframes": ["M15"],
            "history_bars": 3,
        }
    )


def check(strategy: Strategy[Any]) -> list[str]:
    return contract.check_strategy_contract(
        strategy, manifest(strategy.strategy_id), GOLD, CANDLES, TIMES
    )


def test_conforming_strategy_has_no_violation() -> None:
    assert check(Conforming(NoParameters())) == []


def test_hidden_state_is_reported_as_order_dependence() -> None:
    problems = check(Memory(NoParameters()))
    assert any("evaluation order" in problem for problem in problems)


def test_changing_answers_are_reported_as_non_determinism() -> None:
    problems = check(Flipping(NoParameters()))
    assert any("non-deterministic" in problem for problem in problems)


def test_look_ahead_in_the_evaluation_path_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def leaky_evaluate(
        strategy: Strategy[Any],
        manifest: StrategyManifest,
        symbol: str,
        candles: Mapping[Timeframe, Sequence[Candle]],
        evaluated_at: datetime,
    ) -> Outcome:
        seen = len(candles[Timeframe.M15])
        return Outcome(OutcomeKind.NO_SIGNAL, detail=f"saw {seen} candles")

    monkeypatch.setattr(contract, "evaluate", leaky_evaluate)
    problems = check(Conforming(NoParameters()))
    assert any("future candles" in problem for problem in problems)


def test_violations_name_the_evaluation_time() -> None:
    problems = check(Memory(NoParameters()))
    assert problems
    assert all(any(at.isoformat() in problem for at in TIMES) for problem in problems)
