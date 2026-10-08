"""TASK-066: a campaign that evaluates the nine gates of §49 and prices its own selection.

The order matters and is tested here: select on robustness, measure the p-value of every
candidate on the validation window, then -- only if asked -- unlock each market's sealed set
once for its winner. The promotion control must then have a number to check instead of
silently skipping Monte-Carlo, and the false-discovery correction must pay for every
(candidate, market) pair the campaign tried.
"""

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradingagent.analytics.model import Performance, Trade
from tradingagent.analytics.performance import compute_performance
from tradingagent.backtest.costs import CostModel
from tradingagent.backtest.datasets import CandleDataset, SyntheticRegime, synthetic_dataset
from tradingagent.backtest.harness import BacktestConfig
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import ValidationStage
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.campaign import CandidateSpec, run_campaign
from tradingagent.research.promotion import (
    AcceptanceThresholds,
    evaluate_promotion,
    evidence_from_campaign,
)
from tradingagent.research.protocol import (
    GateStatus,
    StabilityReport,
    WalkForwardPlan,
)
from tradingagent.strategies.library.witness import Witness, WitnessParameters
from tradingagent.strategies.manifest import StrategyManifest

START = datetime(2026, 1, 5, tzinfo=UTC)
M15 = Timeframe.M15
BARS = 1_000
#: Short folds on purpose: a validation block must still hold the manifest's 100 history bars,
#: and the rolling origin must fit several folds inside the 800 non-sealed bars.
PLAN = WalkForwardPlan(train_bars=60, validation_bars=150, step_bars=100, max_folds=6)
THRESHOLDS = AcceptanceThresholds(version="test-campaign", min_trades=30)
DECIDED_AT = datetime(2026, 10, 8, 6, 0, tzinfo=UTC)
STAGES = tuple(ValidationStage)


def witness_manifest() -> StrategyManifest:
    return StrategyManifest.model_validate(
        {
            "strategy_id": "witness",
            "version": "1.0.0",
            "max_mode": "SIGNAL",
            "allowed_symbols": ["frxXAUUSD", "cryBTCUSD"],
            "timeframes": ["M15"],
            "history_bars": 100,
            "expiry_bars": 2,
        }
    )


def witness_factory(parameters: Mapping[str, float]) -> Witness:
    return Witness(WitnessParameters(**parameters))


def spec(label: str, fast: int, slow: int, rr: float = 2.0) -> CandidateSpec:
    return CandidateSpec(
        label=label,
        manifest=witness_manifest(),
        factory=witness_factory,
        parameters={
            "ema_fast": fast,
            "ema_slow": slow,
            "atr_period": 14,
            "stop_atr_multiplier": 1.5,
            "take_profit_rr": rr,
            "entry_zone_atr": 0.1,
        },
    )


def candidates() -> list[CandidateSpec]:
    return [spec("fast-1.5R", 10, 30, 1.5), spec("balanced-2R", 20, 50, 2.0)]


def dataset(market: str, seed: int, start_price: float) -> CandleDataset:
    return synthetic_dataset(
        f"test-{market}",
        market,
        M15,
        START,
        (SyntheticRegime(bars=BARS, drift=0.00002, volatility=0.0013),),
        seed=seed,
        start_price=start_price,
    )


def two_markets() -> dict[str, CandleDataset]:
    return {
        "frxXAUUSD": dataset("frxXAUUSD", 11, 2000.0),
        "cryBTCUSD": dataset("cryBTCUSD", 12, 60_000.0),
    }


def prepend_history(one: CandleDataset, bars: int) -> CandleDataset:
    """The same tape with `bars` older candles in front: what a longer fetch produces.

    The prepended block is generated on the same grid and shifted back in time, so the
    concatenation stays a strictly ordered, gap-free M15 series; the point under test is where
    the newest windows land, not the price path of the older bars.
    """
    older = synthetic_dataset(
        f"test-older-{bars}",
        one.symbol,
        M15,
        START,
        (SyntheticRegime(bars=bars, drift=0.00002, volatility=0.0012),),
        seed=99,
        start_price=one.candles[0].close,
    )
    shift = one.candles[0].open_time - older.candles[-1].close_time - timedelta(seconds=900)
    moved = tuple(
        replace(
            candle,
            open_time=candle.open_time + shift,
        )
        for candle in older.candles
    )
    return CandleDataset(
        dataset_id=f"{one.dataset_id}+{bars}-prepended",
        symbol=one.symbol,
        timeframe=one.timeframe,
        source=f"test:prepended+{bars}",
        candles=moved + tuple(one.candles),
    )


def longer_tape() -> CandleDataset:
    """A 3 000-bar tape: three 1 000-bar blocks glued into one ordered M15 series."""
    blocks = [dataset("frxXAUUSD", seed, 2000.0) for seed in (11, 13, 15)]
    step = timedelta(seconds=900 * BARS)
    candles = tuple(
        replace(candle, open_time=candle.open_time + step * index)
        for index, block in enumerate(blocks)
        for candle in block.candles
    )
    return CandleDataset(
        dataset_id="test-long-frxXAUUSD",
        symbol="frxXAUUSD",
        timeframe=M15,
        source="test:long",
        candles=candles,
    )


def config_for(market: str, one_dataset: CandleDataset) -> BacktestConfig:
    price = one_dataset.candles[0].close
    return BacktestConfig(
        symbol=market,
        costs=CostModel(
            spread=round(price * 0.00005, 6),
            slippage_fixed=round(price * 0.00002, 6),
            commission_per_trade=Decimal("0.5"),
        ),
        mode=TradingMode.SIGNAL,
        max_concurrent_positions=1,
    )


def campaign(**overrides: object):
    settings: dict = {
        "datasets": {"frxXAUUSD": dataset("frxXAUUSD", 11, 2000.0)},
        "candidates": candidates(),
        "config_for": config_for,
        "thresholds": THRESHOLDS,
        "walk_forward_plan": PLAN,
        "monte_carlo_iterations": 200,
    }
    settings.update(overrides)
    return run_campaign(**settings)


# --------------------------------------------------------------------------------------
# The gates that a bare backtest never reached
# --------------------------------------------------------------------------------------


def test_the_campaign_decides_walk_forward_monte_carlo_stress_and_robustness() -> None:
    report = campaign()
    assert tuple(item.stage for item in report.gate_verdicts) == STAGES
    verdicts = {item.stage: item for item in report.gate_verdicts}
    for stage in (
        ValidationStage.WALK_FORWARD,
        ValidationStage.MONTE_CARLO,
        ValidationStage.STRESS,
        ValidationStage.PARAMETER_ROBUSTNESS,
    ):
        assert verdicts[stage].evaluable is True, stage
        assert verdicts[stage].evidence, stage
        assert verdicts[stage].evaluator.strip(), stage
        assert verdicts[stage].threshold is not None, stage


def test_the_walk_forward_gate_replays_real_folds_of_the_rolling_origin() -> None:
    report = campaign()
    market = report.markets[0]
    assert market.selected is not None
    for candidate in market.candidates:
        assert candidate.gates is not None
        outcome = candidate.gates.walk_forward
        assert outcome.folds >= 2
        assert 0 <= outcome.profitable_folds <= outcome.folds
        assert outcome.ratio == outcome.profitable_folds / outcome.folds
        assert outcome.trades >= 0
        verdict = candidate.verdict(ValidationStage.WALK_FORWARD)
        assert verdict is not None
        assert verdict.evidence["folds"] == outcome.folds


def test_the_monte_carlo_gate_produces_a_probability_and_a_p_value_for_every_candidate() -> None:
    report = campaign()
    for market in report.markets:
        assert market.winner is not None
        for candidate in market.candidates:
            assert candidate.gates is not None
            probability = candidate.gates.probability_of_profit
            assert 0.0 <= probability <= 1.0
            assert 0.0 < candidate.gates.p_value <= 1.0
            verdict = candidate.verdict(ValidationStage.MONTE_CARLO)
            assert verdict is not None
            assert verdict.evidence["probability_of_profit"] == probability
            assert verdict.evidence["p_value"] == candidate.gates.p_value
            assert verdict.evidence["measurement"] == (
                "validation window, measured before any holdout was opened"
            )


def test_the_stress_gate_is_measured_on_the_doubled_costs_and_named_as_such() -> None:
    report = campaign()
    verdict = report.verdict(ValidationStage.STRESS)
    assert verdict is not None
    assert verdict.status in {GateStatus.PASSED, GateStatus.FAILED}
    for market in verdict.evidence["markets"].values():
        assert market["cost_multiplier"] == 2.0
        assert "profit_factor" in market
        assert market["criteria_borrowed_from"] == ValidationStage.COSTS.value


def test_the_campaign_reports_the_two_gates_it_cannot_measure() -> None:
    report = campaign()
    for stage in (ValidationStage.PAPER, ValidationStage.RISK):
        verdict = report.verdict(stage)
        assert verdict is not None
        assert verdict.status is GateStatus.NOT_EVALUABLE
        assert verdict.evaluable is False
        assert verdict.evidence == {}
        assert verdict.threshold is None
    paper = report.verdict(ValidationStage.PAPER)
    assert paper is not None
    assert "thirty calendar" in paper.reason
    risk = report.verdict(ValidationStage.RISK)
    assert risk is not None
    assert "risk engine" in risk.reason


def test_the_default_walk_forward_plan_gives_every_fold_enough_bars_to_trade() -> None:
    # A validation block shorter than the manifest's history is *skipped*: the gate then says
    # `NOT_EVALUABLE`, which is honest but useless. The default plan must therefore have a
    # block wide enough for the reference strategy to trade, or the gate measures the window.
    from tradingagent.research.campaign import DEFAULT_WALK_FORWARD_PLAN

    assert DEFAULT_WALK_FORWARD_PLAN.validation_bars > witness_manifest().history_bars
    report = campaign(datasets=two_markets(), walk_forward_plan=DEFAULT_WALK_FORWARD_PLAN)
    verdict = report.verdict(ValidationStage.WALK_FORWARD)
    assert verdict is not None
    assert verdict.status is not GateStatus.NOT_EVALUABLE
    for market in verdict.evidence["markets"].values():
        assert market["folds"] >= 2
        assert market["skipped_folds"] == 0


def test_the_default_plan_has_no_fold_ceiling() -> None:
    """A ceiling makes the fold count a constant, so the fold set can never follow history.

    Measured cost: the `max_folds=6` ceiling held the campaign to the first 1 600 bars of the
    rolling window; without it the rolling origin walks to the end of the available history,
    which is the *only* way the walk-forward gate can see recent bars at all.
    """
    from tradingagent.research.campaign import DEFAULT_WALK_FORWARD_PLAN

    assert DEFAULT_WALK_FORWARD_PLAN.max_folds is None


def test_the_default_plan_walks_the_rolling_origin_to_its_newest_bar() -> None:
    """Integration proof, on the campaign's own default plan.

    The rolling window of a market is train + validation (the sealed set is excluded, as it
    must be). Unbounded, the origin advances until the last validation block is the newest block
    that window can offer. Measured on a 3 000-bar tape, where a 6-fold ceiling is already
    binding: 60 % of 3 000 bars roll (2 400), and 350 + 250 bars per fold with a 200-bar step
    fits 10 folds, not 6.
    """
    from tradingagent.research.campaign import DEFAULT_WALK_FORWARD_PLAN

    report = campaign(
        datasets={"frxXAUUSD": longer_tape()}, walk_forward_plan=DEFAULT_WALK_FORWARD_PLAN
    )
    for market in report.markets:
        windows = {window.name: window for window in market.split_windows}
        assert market.dataset_window is not None
        assert market.dataset_window.bars == 3_000
        # The rolling window stops where the sealed set opens.
        assert windows["holdout"].start >= windows["validation"].end
        winner = market.winner
        assert winner is not None and winner.gates is not None
        outcome = winner.gates.walk_forward
        assert outcome.skipped_folds == 0
        rolling = windows["train"].bars + windows["validation"].bars
        assert rolling == 2_400
        # 10 folds of a 200-bar step reach the end of a 2 400-bar rolling window
        # (600 + 9 * 200 + 600 == 2 400): a 6-fold ceiling would have hidden four of them.
        assert outcome.folds == (rolling - 600) // 200 + 1 == 10
        verdict = winner.verdict(ValidationStage.WALK_FORWARD)
        assert verdict is not None
        assert verdict.evidence["folds"] == outcome.folds
        assert verdict.evidence["rolling_bars"] == rolling
        assert verdict.evidence["measurement"] == (
            "validation blocks of the rolling origin, never the sealed set"
        )


def test_a_campaign_reports_the_windows_it_measured_and_keeps_them_anchored() -> None:
    """The second defect: prepending history must not slide the newest windows backwards.

    A 1 000-bar campaign and a 4 000-bar campaign (the same tape with 3 000 older bars in front)
    must end on the same bar, and the longer run's tail must contain everything the shorter run
    read as validation and as holdout. `anchor=False` restores the old sliding cut, so the
    change is opt-outable rather than forced -- and there the older run's validation window ends
    *before* the newer run's sealed window opens, which is the confound this removes.
    """
    one = dataset("frxXAUUSD", 11, 2000.0)
    short = campaign(datasets={"frxXAUUSD": one})
    longer = campaign(datasets={"frxXAUUSD": prepend_history(one, 3_000)})
    short_windows = {window.name: window for window in short.markets[0].split_windows}
    long_windows = {window.name: window for window in longer.markets[0].split_windows}
    assert set(short_windows) == {"train", "validation", "holdout"}
    assert long_windows["holdout"].end == short_windows["holdout"].end == one.candles[-1].close_time
    assert long_windows["train"].bars > short_windows["train"].bars
    assert long_windows["validation"].bars > short_windows["validation"].bars
    # The anchored tail starts where its training window ends, and it starts earlier than the
    # unanchored one would: that is the training window absorbing the newly fetched history.
    assert long_windows["validation"].start == long_windows["train"].end
    assert long_windows["validation"].start <= one.candles[0].open_time
    assert short_windows["train"].anchored is True
    sliding = campaign(anchor=False)
    sliding_windows = {window.name: window for window in sliding.markets[0].split_windows}
    assert sliding_windows["holdout"].anchored is False
    assert sliding_windows["holdout"].end == short_windows["holdout"].end


def test_a_campaign_reports_the_dataset_window_it_measured_over() -> None:
    """`dataset_window` is the whole tape, `split_windows` the three parts of it."""
    report = campaign()
    market = report.markets[0]
    one = dataset("frxXAUUSD", 11, 2000.0)
    assert market.dataset_window.bars == one.bars == BARS
    assert market.dataset_window.start == one.candles[0].open_time
    assert market.dataset_window.end == one.candles[-1].close_time
    assert sum(window.bars for window in market.split_windows) == BARS


def test_folds_too_short_to_trade_are_skipped_rather_than_counted_as_losses() -> None:
    narrow = WalkForwardPlan(train_bars=60, validation_bars=60, step_bars=10, max_folds=4)
    report = campaign(walk_forward_plan=narrow)
    for market in report.markets:
        assert market.winner is not None
        assert market.winner.gates is not None
        outcome = market.winner.gates.walk_forward
        assert outcome.folds == 0
        assert outcome.skipped_folds == 4  # counted, not scored
        assert outcome.ratio == 0.0
        verdict = market.verdict(ValidationStage.WALK_FORWARD)
        assert verdict is not None
        assert verdict.status is GateStatus.NOT_EVALUABLE
        assert "fewer bars than the manifest" in verdict.reason
    campaign_level = report.verdict(ValidationStage.WALK_FORWARD)
    assert campaign_level is not None
    assert campaign_level.status is GateStatus.NOT_EVALUABLE


# --------------------------------------------------------------------------------------
# The sealed set: selection reads nothing, confirmation reads once
# --------------------------------------------------------------------------------------


def test_without_confirmation_the_holdout_stays_sealed_and_the_gate_says_so() -> None:
    report = campaign()
    for market in report.markets:
        assert market.holdout_still_sealed is True
        assert market.holdout_unlocks == 0
        verdict = market.verdict(ValidationStage.OUT_OF_SAMPLE)
        assert verdict is not None
        assert verdict.status is GateStatus.NOT_EVALUABLE
        assert "never unlocked" in verdict.reason


def test_confirming_the_holdout_reads_it_once_for_the_selected_candidate_only() -> None:
    report = campaign(confirm_holdout=True)
    for market in report.markets:
        assert market.holdout_still_sealed is False
        assert market.holdout_unlocks == 1  # once, and only once
        assert market.out_of_sample is not None
        winner = market.winner
        assert winner is not None
        assert winner.gates is not None
        assert winner.gates.out_of_sample is market.out_of_sample
        verdict = market.verdict(ValidationStage.OUT_OF_SAMPLE)
        assert verdict is not None
        assert verdict.evaluable is True
        assert verdict.evidence["holdout_read"] is True
        assert verdict.evidence["candidate"] == winner.label
        # An unselected candidate was never unlocked: its holdout verdict stays open.
        for candidate in market.candidates:
            if candidate.selected:
                continue
            other = candidate.verdict(ValidationStage.OUT_OF_SAMPLE)
            assert other is not None
            assert other.status is GateStatus.NOT_EVALUABLE
            assert other.evidence["holdout_read"] is False


# --------------------------------------------------------------------------------------
# Multiple testing: every attempt is paid for
# --------------------------------------------------------------------------------------


def test_every_candidate_market_pair_is_one_hypothesis_of_the_correction() -> None:
    report = campaign(datasets=two_markets())
    assert len(report.hypotheses) == 4  # 2 markets x 2 candidates
    assert report.multiple_testing is not None
    assert report.multiple_testing.hypotheses == 4
    assert report.multiple_testing.method == "benjamini_hochberg"
    assert report.multiple_testing.expected_false_discoveries == pytest.approx(0.4)
    assert report.multiple_testing.bonferroni_threshold == pytest.approx(0.10 / 4)
    for label, p_value in report.hypotheses:
        assert p_value is not None  # a campaign always produces its p-value
        assert ":" in label


def test_a_demoted_candidate_loses_its_claim_and_never_the_figures_it_measured() -> None:
    report = campaign(datasets=two_markets())
    everyone = [candidate for market in report.markets for candidate in market.candidates]
    demoted = [
        candidate
        for candidate in everyone
        if candidate.gates is not None and not candidate.gates.significant
    ]
    assert demoted, "the correction demoted nobody, so this test proves nothing"
    # The claim is what the correction can revoke: `claims()` is the set a promotion may rest
    # on, and a demoted candidate is not in it.
    assert all(candidate not in report.claims() for candidate in demoted)
    assert len(report.claims()) == len(everyone) - len(demoted)
    for candidate in demoted:
        assert candidate.gates is not None
        # The p-value and the rank threshold that demoted it are kept, so the decision can be
        # audited; the numbers of the backtest are untouched, because a correction of the
        # selection must not rewrite a measurement.
        assert candidate.gates.p_value is not None
        assert candidate.verdict(ValidationStage.MONTE_CARLO) is not None
        assert candidate.gates.monte_carlo.trades >= 0
    # And the reverse: a candidate the correction kept is a claim of this campaign.
    for candidate in report.claims():
        assert candidate.gates is not None
        assert candidate.gates.significant is True


def test_the_gate_table_keeps_one_evaluator_per_stage_after_the_correction() -> None:
    report = campaign(datasets=two_markets())
    for verdict in report.gate_verdicts:
        for part in verdict.evaluator.split(" | "):
            assert part.strip()
        assert len(verdict.evaluator.split(" | ")) == len(set(verdict.evaluator.split(" | ")))


def test_the_campaign_report_is_deterministic() -> None:
    first = campaign()
    second = campaign()
    assert first.hypotheses == second.hypotheses
    for market in first.markets:
        for candidate in market.candidates:
            assert candidate.gates is not None
            twin = next(
                item for item in second.markets[0].candidates if item.label == candidate.label
            )
            assert twin.gates is not None
            assert candidate.gates.p_value == twin.gates.p_value


# --------------------------------------------------------------------------------------
# The promotion control is no longer skipped
# --------------------------------------------------------------------------------------


def stability(trades: int = 40, retention: float = 0.9) -> StabilityReport:
    return StabilityReport(
        score=0.9,
        out_of_sample_retention=retention,
        parameter_dispersion=0.1,
        profitable_regime_ratio=1.0,
        trades=trades,
        fragile=False,
        reasons=(),
    )


def performance_of(pnls: list[float]) -> Performance:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    trades = [
        Trade(
            symbol="frxXAUUSD",
            strategy_ref="witness@1.0.0",
            direction=Direction.BUY,
            timeframe=M15,
            mode=TradingMode.SIGNAL,
            opened_at=base + timedelta(minutes=index),
            closed_at=base + timedelta(minutes=index + 1),
            pnl_eur=Decimal(str(pnl)),
            risk_eur=Decimal("10"),
        )
        for index, pnl in enumerate(pnls)
    ]
    return compute_performance(trades)


def clean() -> Performance:
    return performance_of([1.5] * 20 + [-1.0] * 10)


def test_promotion_now_checks_the_monte_carlo_probability_it_used_to_skip() -> None:
    thresholds = AcceptanceThresholds(version="test", min_trades=30)
    weak = evidence_from_campaign(
        "witness@1.0.0",
        {"ema_fast": 20.0},
        clean(),
        clean(),
        clean(),
        stability(),
        thresholds,
        monte_carlo_probability_of_profit=0.20,
    )
    decision = evaluate_promotion(weak, thresholds, decided_at=DECIDED_AT, decided_by="test")
    assert decision.promoted is False
    assert any("Monte-Carlo probability of profit 0.20" in reason for reason in decision.reasons)


def test_a_probability_that_clears_the_threshold_is_not_a_reason_to_refuse() -> None:
    thresholds = AcceptanceThresholds(version="test", min_trades=30)
    strong = evidence_from_campaign(
        "witness@1.0.0",
        {"ema_fast": 20.0},
        clean(),
        clean(),
        clean(),
        stability(),
        thresholds,
        monte_carlo_probability_of_profit=0.83,
    )
    decision = evaluate_promotion(strong, thresholds, decided_at=DECIDED_AT, decided_by="test")
    assert decision.promoted is True
    assert decision.reasons == ()


def test_the_campaign_hands_its_own_probability_to_the_promotion_evidence() -> None:
    report = campaign(confirm_holdout=True)
    market = report.markets[0]
    winner = market.winner
    assert winner is not None and winner.gates is not None and market.out_of_sample is not None
    assert winner.stability_report is not None
    probability = winner.gates.probability_of_profit
    evidence = evidence_from_campaign(
        "witness@1.0.0",
        dict(winner.parameters),
        winner.train,
        market.out_of_sample,
        winner.cost_net,
        winner.stability_report,
        THRESHOLDS,
        monte_carlo_probability_of_profit=probability,
    )
    assert evidence.monte_carlo_probability_of_profit == probability
    decision = evaluate_promotion(
        evidence, THRESHOLDS, decided_at=DECIDED_AT, decided_by="test-campaign"
    )
    assert decision.promoted is (not decision.reasons)
    if probability < THRESHOLDS.min_monte_carlo_probability:
        assert any("Monte-Carlo probability" in reason for reason in decision.reasons)
