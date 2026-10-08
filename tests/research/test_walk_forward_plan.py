"""A gate that never plays a fold is not a verdict, it is a silence.

On 2026-10-08 both deployed strategies reported `walk_forward 0/0, 45 skipped`: the default
plan measures a 250-bar trading window, the harness is fed the validation block alone, and it
refuses a block that cannot first serve the history the manifest declares (300 bars for
`witness`, 600 for `trend_breakout`). The gate that exists to contradict a selection was mute
for exactly the strategies in production.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tradingagent.backtest.costs import CostModel
from tradingagent.backtest.datasets import SyntheticRegime, synthetic_dataset
from tradingagent.backtest.harness import BacktestConfig, run_backtest
from tradingagent.core.market import Candle
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.campaign import (
    DEFAULT_WALK_FORWARD_PLAN,
    CandidateSpec,
    _walk_forward_folds,
    walk_forward_plan_for,
)
from tradingagent.research.protocol import walk_forward
from tradingagent.strategies.library.witness import Witness, WitnessParameters
from tradingagent.strategies.manifest import StrategyManifest

MARKET = "XAUUSD"
PARAMETERS = {
    "ema_fast": 20,
    "ema_slow": 50,
    "atr_period": 14,
    "stop_atr_multiplier": 1.5,
    "take_profit_rr": 2.0,
    "entry_zone_atr": 0.1,
}


def a_manifest(history_bars: int) -> StrategyManifest:
    return StrategyManifest.model_validate(
        {
            "strategy_id": "witness",
            "version": "1.1.1",
            "max_mode": "SIGNAL",
            "allowed_symbols": [MARKET],
            "timeframes": ["M15"],
            "history_bars": history_bars,
            "expiry_bars": 1,
        }
    )


def a_dataset(bars: int = 3000):
    return synthetic_dataset(
        "walk-forward-test",
        MARKET,
        Timeframe.M15,
        datetime(2026, 1, 5, tzinfo=UTC),
        (SyntheticRegime(bars=bars, drift=0.00002, volatility=0.0012),),
        seed=17,
        start_price=2000.0,
        decimals=2,
    )


def a_config() -> BacktestConfig:
    return BacktestConfig(
        symbol=MARKET,
        costs=CostModel(spread=0.1, slippage_fixed=0.04, commission_per_trade=Decimal("0.5")),
        mode=TradingMode.SIGNAL,
        max_concurrent_positions=1,
    )


def test_a_fold_holds_the_warm_up_and_the_window_it_measures() -> None:
    plan = walk_forward_plan_for(a_manifest(300))

    assert plan.validation_bars == 300 + DEFAULT_WALK_FORWARD_PLAN.validation_bars
    assert plan.train_bars >= 300, "the train side must not be shorter than the warm-up"
    assert plan.step_bars == DEFAULT_WALK_FORWARD_PLAN.step_bars, "same rolling origin"


def test_every_declared_history_gets_its_warm_up() -> None:
    for history in (100, 300, 600):
        plan = walk_forward_plan_for(a_manifest(history))
        assert plan.validation_bars >= history + DEFAULT_WALK_FORWARD_PLAN.validation_bars


def test_a_250_bar_block_cannot_feed_a_300_bar_warm_up() -> None:
    """The defect in one call: what the old plan handed the harness, and what it answered."""
    dataset = a_dataset()
    block = list(dataset.candles[: DEFAULT_WALK_FORWARD_PLAN.validation_bars])

    with pytest.raises(ValueError, match="cannot feed"):
        run_backtest(
            Witness(WitnessParameters(**PARAMETERS)),
            a_manifest(300),
            {Timeframe.M15: block},
            a_config(),
        )


def test_the_deployed_history_now_plays_folds_instead_of_skipping_all_of_them() -> None:
    dataset = a_dataset()
    candidate = CandidateSpec(
        label="witness@1.1.1",
        manifest=a_manifest(300),
        factory=lambda values: Witness(WitnessParameters(**values)),
        parameters=dict(PARAMETERS),
    )
    rolling: list[Candle] = list(dataset.candles[: int(len(dataset.candles) * 0.8)])

    folds, skipped = _walk_forward_folds(
        candidate, dataset, a_config(), rolling, DEFAULT_WALK_FORWARD_PLAN
    )

    assert skipped == 0, "the plan is widened inside, so nothing is refused for the warm-up"
    assert len(folds) >= 2, "and the gate has real folds to judge"


def test_the_widened_plan_still_grows_as_the_tape_allows() -> None:
    """Widening a fold must not stop the rolling origin: the newest block is still played."""
    dataset = a_dataset()
    plan = walk_forward_plan_for(a_manifest(300))
    rolling = list(dataset.candles[: int(len(dataset.candles) * 0.8)])

    folds = list(walk_forward(rolling, plan))

    assert len(folds) >= 2
    assert folds[-1].validation[-1].open_time > folds[0].validation[-1].open_time
