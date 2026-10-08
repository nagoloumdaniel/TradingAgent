import random
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tradingagent.config.strategy_catalog import load_strategy_catalog, mode_ceiling_problems
from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import TradingMode, mode_rank
from tradingagent.core.timeframe import Timeframe
from tradingagent.indicators.moving_average import ema
from tradingagent.indicators.volatility import atr
from tradingagent.strategies.contract import check_strategy_contract
from tradingagent.strategies.evaluation import Outcome, OutcomeKind, evaluate
from tradingagent.strategies.library.witness import Witness, WitnessParameters
from tradingagent.strategies.manifest import StrategyManifest
from tradingagent.strategies.registry import REGISTRY

START = datetime(2026, 10, 3, tzinfo=UTC)
GOLD = "frxXAUUSD"
STEP = timedelta(minutes=15)
SHIPPED = Path(__file__).resolve().parents[2] / "config" / "strategies"

SMALL = WitnessParameters(
    ema_fast=3,
    ema_slow=6,
    atr_period=3,
    stop_atr_multiplier=1.5,
    # Deliberately not the shipped 2.0, so a hardcoded ratio cannot pass.
    take_profit_rr=2.5,
    entry_zone_atr=0.1,
)
HISTORY = 30


def manifest(history_bars: int = HISTORY) -> StrategyManifest:
    return StrategyManifest.model_validate(
        {
            "strategy_id": "witness",
            "version": "1.0.0",
            "max_mode": "SIGNAL",
            "allowed_symbols": [GOLD],
            "timeframes": ["M15"],
            "history_bars": history_bars,
        }
    )


def candles_from(closes: Sequence[float]) -> list[Candle]:
    result = []
    previous = closes[0]
    for index, close in enumerate(closes):
        result.append(
            Candle(
                Timeframe.M15,
                START + STEP * index,
                previous,
                max(previous, close) + 0.5,
                min(previous, close) - 0.5,
                close,
            )
        )
        previous = close
    return result


def close_of(index: int) -> datetime:
    return START + STEP * (index + 1)


def replay(strategy: Witness, closes: Sequence[float], history: int = HISTORY) -> list[Outcome]:
    candles = {Timeframe.M15: candles_from(closes)}
    built = manifest(history)
    return [
        evaluate(strategy, built, GOLD, candles, close_of(index))
        for index in range(history - 1, len(closes))
    ]


def window_cross(closes: Sequence[float], index: int) -> Direction | None:
    """Independent oracle: the crossover as seen on the exact evaluation window."""
    window = list(closes[index - HISTORY + 1 : index + 1])
    fast, slow = ema(window, SMALL.ema_fast), ema(window, SMALL.ema_slow)
    assert None not in (fast[-2], slow[-2], fast[-1], slow[-1])
    if fast[-2] <= slow[-2] and fast[-1] > slow[-1]:  # type: ignore[operator]
        return Direction.BUY
    if fast[-2] >= slow[-2] and fast[-1] < slow[-1]:  # type: ignore[operator]
        return Direction.SELL
    return None


DOWN_THEN_UP = [100.0 - index for index in range(30)] + [71.0 + 3 * index for index in range(15)]
UP_THEN_DOWN = [200.0 - close for close in DOWN_THEN_UP]


@pytest.mark.parametrize(
    ("closes", "direction"), [(DOWN_THEN_UP, Direction.BUY), (UP_THEN_DOWN, Direction.SELL)]
)
def test_signals_exactly_on_the_crossover(closes: list[float], direction: Direction) -> None:
    outcomes = replay(Witness(SMALL), closes)
    signals = [
        (index, outcome)
        for index, outcome in zip(range(HISTORY - 1, len(closes)), outcomes, strict=True)
        if outcome.kind is OutcomeKind.SIGNAL
    ]
    expected = [index for index in range(HISTORY - 1, len(closes)) if window_cross(closes, index)]
    assert [index for index, _ in signals] == expected
    assert len(expected) == 1
    assert signals[0][1].candidate is not None
    assert signals[0][1].candidate.direction is direction


@pytest.mark.parametrize("closes", [DOWN_THEN_UP, UP_THEN_DOWN])
def test_levels_derive_from_the_window_atr(closes: list[float]) -> None:
    outcomes = replay(Witness(SMALL), closes)
    index, outcome = next(
        (index, outcome)
        for index, outcome in zip(range(HISTORY - 1, len(closes)), outcomes, strict=True)
        if outcome.kind is OutcomeKind.SIGNAL
    )
    window = candles_from(closes)[index - HISTORY + 1 : index + 1]
    volatility = atr(
        [c.high for c in window], [c.low for c in window], [c.close for c in window], 3
    )[-1]
    assert volatility is not None
    close = closes[index]
    candidate = outcome.candidate
    assert candidate is not None
    sign = 1 if candidate.direction is Direction.BUY else -1
    assert candidate.entry_low == pytest.approx(close - 0.1 * volatility)
    assert candidate.entry_high == pytest.approx(close + 0.1 * volatility)
    assert candidate.stop_loss == pytest.approx(close - sign * 1.5 * volatility)
    assert candidate.take_profits == pytest.approx((close + sign * 2.5 * 1.5 * volatility,))
    assert candidate.indicators["atr"] == pytest.approx(volatility)
    assert "EMA3" in candidate.reason and "EMA6" in candidate.reason


def test_no_signal_without_a_crossover() -> None:
    rising = [50.0 + index for index in range(60)]
    assert all(outcome.kind is OutcomeKind.NO_SIGNAL for outcome in replay(Witness(SMALL), rising))


def random_walk(length: int, seed: int) -> list[float]:
    generator = random.Random(seed)
    closes = [1000.0]
    for _ in range(length - 1):
        closes.append(max(1.0, closes[-1] + generator.gauss(0, 4)))
    return closes


def test_respects_the_strategy_contract() -> None:
    closes = random_walk(120, seed=7)
    candles = {Timeframe.M15: candles_from(closes)}
    times = [close_of(index) for index in range(HISTORY - 1, len(closes))]
    assert check_strategy_contract(Witness(SMALL), manifest(), GOLD, candles, times) == []


def test_replay_with_shipped_parameters_produces_valid_buys_and_sells() -> None:
    shipped = load_strategy_catalog(SHIPPED, REGISTRY)["witness@1.0.0"]
    closes = random_walk(2_000, seed=42)
    candles = {Timeframe.M15: candles_from(closes)}
    history = shipped.manifest.history_bars
    outcomes = [
        evaluate(shipped.strategy, shipped.manifest, GOLD, candles, close_of(index))
        for index in range(history - 1, len(closes))
    ]
    assert not any(outcome.kind.is_strategy_error for outcome in outcomes)
    directions = {o.candidate.direction for o in outcomes if o.candidate is not None}
    assert directions == {Direction.BUY, Direction.SELL}


def test_shipped_witness_manifests_document_every_ceiling_above_signal() -> None:
    """The invariant is "no silent escalation", not "never above SIGNAL".

    `witness@1.1.1` carries the operator derogation of 2026-10-08: a DEMO rehearsal on a
    demonstration account, with the campaign's figures written in its header. That is a
    documented decision, so it is allowed -- and the catalog refuses the same ceiling on any
    manifest that does not carry the banner. `test_mode_ceiling.py` exercises the rule itself,
    both halves; this test pins it to the manifests that actually ship.
    """
    catalog = load_strategy_catalog(SHIPPED, REGISTRY)
    witnesses = [loaded for loaded in catalog.values() if loaded.manifest.strategy_id == "witness"]
    assert witnesses
    for loaded in witnesses:
        path = SHIPPED / f"{loaded.manifest.ref}.yaml"
        text = path.read_text(encoding="utf-8")
        assert mode_ceiling_problems(text, loaded.manifest.max_mode) == []
        assert mode_rank(loaded.manifest.max_mode) <= mode_rank(TradingMode.LIVE)
    # DEMO is a bounded ceiling: it is below LIVE, which the real account requires.
    assert mode_rank(TradingMode.DEMO) < mode_rank(TradingMode.LIVE)


def test_shipped_manifest_includes_the_warm_up() -> None:
    shipped = load_strategy_catalog(SHIPPED, REGISTRY)["witness@1.0.0"]
    parameters = shipped.strategy.parameters
    assert isinstance(parameters, WitnessParameters)
    assert shipped.manifest.history_bars >= 5 * max(parameters.ema_slow, parameters.atr_period)


def test_witness_is_in_the_production_registry() -> None:
    assert REGISTRY["witness"] is Witness


@pytest.mark.parametrize(
    "overrides",
    [
        {"ema_slow": 3},
        {"ema_slow": 2},
        {"entry_zone_atr": 1.5},
        {"take_profit_rr": 0.05},
        {"stop_atr_multiplier": 0},
        {"atr_period": 0},
        {"typo": 1},
    ],
)
def test_incoherent_parameters_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        WitnessParameters.model_validate({**SMALL.model_dump(), **overrides})
