"""TASK-066: the campaign script must hand a Monte-Carlo probability to the promotion control.

`evaluate_promotion` only checks the probability when it is not `None`, so a campaign that
forgets the argument drops a promotion gate without saying so. These tests pin the script's
own evidence builder -- the one place that argument is passed -- and the guard that refuses to
decide on evidence where it is missing.
"""

import importlib.util
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tradingagent.analytics.model import Performance, Trade
from tradingagent.analytics.performance import compute_performance
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import ValidationStage
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.campaign import (
    CampaignReport,
    CandidateGates,
    CandidateReport,
    CorrelationPair,
    MarketReport,
)
from tradingagent.research.promotion import (
    AcceptanceThresholds,
    PromotionEvidence,
    evaluate_promotion,
)
from tradingagent.research.protocol import (
    DataWindow,
    GateStatus,
    GateVerdict,
    MonteCarloReport,
    StabilityReport,
    WalkForwardOutcome,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "backtest" / "run_campaign.py"
START = datetime(2026, 1, 1, tzinfo=UTC)
M15 = Timeframe.M15
THRESHOLDS = AcceptanceThresholds(version="script-test", min_trades=30)


def load_script() -> ModuleType:
    """Load `scripts/backtest/run_campaign.py` as a module, the way an operator runs it."""
    spec = importlib.util.spec_from_file_location("run_campaign_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def performance_of(pnls: list[float]) -> Performance:
    trades = [
        Trade(
            symbol="XAUUSD",
            strategy_ref="witness@1.0.0",
            direction=Direction.BUY,
            timeframe=M15,
            mode=TradingMode.SIGNAL,
            opened_at=START + timedelta(minutes=index),
            closed_at=START + timedelta(minutes=index + 1),
            pnl_eur=Decimal(str(pnl)),
            risk_eur=Decimal("10"),
        )
        for index, pnl in enumerate(pnls)
    ]
    return compute_performance(trades)


def traded() -> Performance:
    return performance_of([1.5] * 20 + [-1.0] * 10)


def candidate(probability: float, trades: int = 40, selected: bool = True) -> CandidateReport:
    stability = StabilityReport(
        score=0.8,
        out_of_sample_retention=0.9,
        parameter_dispersion=0.1,
        profitable_regime_ratio=1.0,
        trades=trades,
        fragile=False,
        reasons=(),
    )
    gates = CandidateGates(
        walk_forward=WalkForwardOutcome(
            folds=6, profitable_folds=4, trades=60, net_profit=Decimal("40")
        ),
        perturbed=(traded(), traded()),
        monte_carlo=MonteCarloReport(
            iterations=200,
            trades=trades,
            net_profit_p05=-2.0,
            net_profit_median=25.0,
            net_profit_p95=50.0,
            probability_of_profit=probability,
            median_max_drawdown=8.0,
            worst_max_drawdown=19.0,
        ),
        p_value=0.02,
        stressed=traded(),
        out_of_sample=traded(),
    )
    return CandidateReport(
        market="XAUUSD",
        label="balanced-2R",
        train=traded(),
        validation=traded(),
        cost_net=traded(),
        stability_score=stability.score,
        fragile=stability.fragile,
        reasons=(),
        selected=selected,
        stability_report=stability,
        parameters={"ema_fast": 20.0},
        gates=gates,
    )


def verdicts(report: CandidateReport) -> dict[ValidationStage, GateVerdict]:
    from tradingagent.research.campaign import candidate_gate_verdicts

    return {item.stage: item for item in candidate_gate_verdicts(report, THRESHOLDS)}


def campaign_report(probability: float = 0.62) -> tuple[CampaignReport, MarketReport]:
    from tradingagent.research.campaign import evaluate_campaign_gates, rebuild_market_verdicts

    winner = candidate(probability)
    market = MarketReport(
        market="XAUUSD",
        dataset_id="script-test",
        timeframe=M15,
        candidates=(winner,),
        selected=winner.label,
        holdout_still_sealed=False,
        out_of_sample=traded(),
        holdout_unlocks=1,
        gate_verdicts=rebuild_market_verdicts((winner,)),
    )
    report = CampaignReport(
        markets=(market,),
        correlations=(CorrelationPair("XAUUSD", "BTCUSD", 0.33, 2_000),),
        gate_verdicts=evaluate_campaign_gates((market,), THRESHOLDS),
    )
    return report, market


def test_the_script_is_loadable_and_exposes_the_evidence_builder() -> None:
    module = load_script()
    assert callable(module.promotion_evidence)
    assert callable(module.require_monte_carlo_evidence)


def test_the_script_passes_the_campaign_probability_into_the_promotion_evidence() -> None:
    module = load_script()
    report, market = campaign_report(probability=0.62)
    winner = market.candidates[0]
    evidence = module.promotion_evidence(report, market, winner, THRESHOLDS)
    assert isinstance(evidence, PromotionEvidence)
    assert evidence.monte_carlo_probability_of_profit == 0.62
    assert evidence.correlation_with_existing == pytest.approx(0.33)
    # And the control really reads it: an empty reason list can only happen when every
    # threshold, Monte-Carlo included, was checked and cleared.
    decision = evaluate_promotion(
        evidence, THRESHOLDS, decided_at=datetime.now(UTC), decided_by="test"
    )
    assert decision.promoted is (not decision.reasons)


def test_a_low_probability_refuses_the_promotion_through_the_script_evidence() -> None:
    module = load_script()
    report, market = campaign_report(probability=0.11)
    evidence = module.promotion_evidence(report, market, market.candidates[0], THRESHOLDS)
    decision = evaluate_promotion(
        evidence, THRESHOLDS, decided_at=datetime.now(UTC), decided_by="test"
    )
    assert decision.promoted is False
    assert any("Monte-Carlo probability of profit 0.11" in reason for reason in decision.reasons)


def test_the_guard_refuses_evidence_without_a_probability() -> None:
    module = load_script()
    report, market = campaign_report()
    evidence = module.promotion_evidence(report, market, market.candidates[0], THRESHOLDS)
    without = PromotionEvidence(
        strategy_ref=evidence.strategy_ref,
        parameters=evidence.parameters,
        in_sample=evidence.in_sample,
        out_of_sample=evidence.out_of_sample,
        cost_net=evidence.cost_net,
        stability=evidence.stability,
        thresholds_digest=evidence.thresholds_digest,
        correlation_with_existing=evidence.correlation_with_existing,
        monte_carlo_probability_of_profit=None,
    )
    with pytest.raises(SystemExit, match="Monte-Carlo"):
        module.require_monte_carlo_evidence(without)
    assert module.require_monte_carlo_evidence(evidence) == 0.62


def test_the_script_reports_every_stage_of_the_gate_protocol() -> None:
    module = load_script()
    report, _market = campaign_report()
    document = module.campaign_to_dict(report, {}, THRESHOLDS)
    stages = [item["stage"] for item in document["gates"]]
    assert stages == [stage.value for stage in ValidationStage]
    assert document["gate_protocol"]["false_discovery_rate"] == 0.10
    assert document["gate_protocol"]["stress_cost_multiplier"] == 2.0
    assert document["gate_protocol"]["monte_carlo_iterations"] == 1_000
    assert document["markets"][0]["candidates"][0]["monte_carlo"]["probability_of_profit"] == 0.62
    # Every market carries its own nine verdicts too, so a reader never has to take the
    # campaign aggregate on trust.
    assert [item["stage"] for item in document["markets"][0]["gates"]] == stages


def test_the_script_publishes_the_windows_a_comparison_must_hold_fixed() -> None:
    """A before/after comparison needs the periods, or it is comparing two market regimes."""
    module = load_script()
    report, market = campaign_report()
    document = module.campaign_to_dict(report, {}, THRESHOLDS)
    # The report fixture predates the windows, so the writers must tolerate their absence and
    # the campaign-level protocol must still state how the tape was cut.
    assert document["gate_protocol"]["split"] == {
        "anchored": True,
        "train_fraction": 0.6,
        "validation_fraction": 0.2,
    }
    assert document["markets"][0]["split_windows"] == []
    assert document["markets"][0]["dataset_window"] is None
    measured = replace(
        market,
        anchored=True,
        dataset_window=DataWindow("dataset", START, START + timedelta(minutes=15), 2, True),
        split_windows=(
            DataWindow("train", START, START + timedelta(minutes=15), 1, True),
            DataWindow("validation", START, START + timedelta(minutes=15), 1, True),
        ),
    )
    windows = module.campaign_to_dict(replace(report, markets=(measured,)), {}, THRESHOLDS)
    published = windows["markets"][0]
    assert published["anchored"] is True
    assert [item["name"] for item in published["split_windows"]] == ["train", "validation"]
    assert published["dataset_window"]["bars"] == 2
    assert published["split_windows"][0]["start"] == START.isoformat()
    assert published["split_windows"][0]["anchored"] is True


def test_the_script_marks_the_two_gates_it_cannot_evaluate() -> None:
    module = load_script()
    report, _ = campaign_report()
    document = module.campaign_to_dict(report, {}, THRESHOLDS)
    by_stage = {item["stage"]: item for item in document["gates"]}
    for stage in ("paper", "risk"):
        assert by_stage[stage]["evaluated"] is False
        assert by_stage[stage]["passed"] is False
        assert by_stage[stage]["status"] == GateStatus.NOT_EVALUABLE.value
        assert by_stage[stage]["threshold"] is None


def test_the_gate_figure_helper_reads_the_worst_market_reading() -> None:
    module = load_script()
    verdict = GateVerdict(
        stage=ValidationStage.WALK_FORWARD,
        status=GateStatus.FAILED,
        evaluator="campaign._walk_forward_folds -> protocol.walk_forward",
        reason="failed",
        threshold=0.5,
        evidence={
            "markets": {
                "XAUUSD": {"ratio": 0.0},
                "BTCUSD": {"ratio": 0.5},
            },
            "failed_markets": ["XAUUSD"],
        },
    )
    assert module._figure(verdict) == "0.00 folds"
    assert module._figure(verdicts(candidate(0.62))[ValidationStage.BACKTEST]).endswith("trades")


def test_a_mapping_of_evidence_is_json_ready() -> None:
    module = load_script()
    report, _ = campaign_report()
    document = module.campaign_to_dict(report, {}, THRESHOLDS)
    gates: Any = document["gates"]
    assert isinstance(gates, list)
    evidence: Mapping[str, Any] = gates[0]["evidence"]
    assert isinstance(evidence, Mapping)
