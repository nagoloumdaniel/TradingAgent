"""Gate-by-gate proof that the campaign evaluates all nine (§49, TASK-066).

Each gate is exercised twice: once on evidence that clears it, once on evidence that cannot.
A gate nobody can decide here (paper trading, the risk engine) must come back `NOT_EVALUABLE`
with the reason and the evidence it would need -- never as a pass, because a gate reported as
passed without proof is the worst possible outcome of a validation campaign.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tradingagent.analytics.model import Performance, Trade
from tradingagent.analytics.performance import compute_performance
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import ValidationStage
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.campaign import (
    CandidateGates,
    CandidateReport,
    candidate_gate_verdicts,
)
from tradingagent.research.promotion import AcceptanceThresholds
from tradingagent.research.protocol import (
    GateStatus,
    GateVerdict,
    MonteCarloReport,
    WalkForwardOutcome,
)

START = datetime(2026, 1, 5, tzinfo=UTC)
M15 = Timeframe.M15
THRESHOLDS = AcceptanceThresholds(version="test", min_trades=30)
STAGES = tuple(ValidationStage)


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


def steady(trades: int = 40, per_trade: float = 1.0) -> Performance:
    """A clean sample: `trades` winners of `per_trade`, no drawdown, a defined factor."""
    return performance_of([per_trade] * trades)


def losing(trades: int = 40, per_trade: float = -1.0) -> Performance:
    return performance_of([per_trade] * trades)


def traded(wins: int = 20, losses: int = 10, win: float = 1.5, loss: float = -1.0) -> Performance:
    """A sample with a *defined* profit factor: 20 winners and 10 losers alternate.

    A run of pure winners has no gross loss, so its profit factor is `None` -- undefined, not
    infinite -- and every gate that reads a factor fails on it. A realistic sample has both.
    """
    pnls: list[float] = []
    for index in range(max(wins, losses)):
        if index < wins:
            pnls.append(win)
        if index < losses:
            pnls.append(loss)
    return performance_of(pnls)


def monte_carlo(probability: float = 0.80, trades: int = 60) -> MonteCarloReport:
    return MonteCarloReport(
        iterations=200,
        trades=trades,
        net_profit_p05=-5.0,
        net_profit_median=20.0,
        net_profit_p95=45.0,
        probability_of_profit=probability,
        median_max_drawdown=10.0,
        worst_max_drawdown=25.0,
    )


def walk_forward_outcome(profitable: int = 4, folds: int = 6) -> WalkForwardOutcome:
    return WalkForwardOutcome(
        folds=folds,
        profitable_folds=profitable,
        trades=folds * 10,
        net_profit=Decimal(str(profitable * 10 - (folds - profitable) * 10)),
    )


def candidate(
    *,
    train: Performance | None = None,
    cost_net: Performance | None = None,
    stressed: Performance | None = None,
    out_of_sample: Performance | None = None,
    retention: float = 0.9,
    dispersion: float = 0.1,
    walk_forward: WalkForwardOutcome | None = None,
    probability: float = 0.80,
    p_value: float = 0.01,
    perturbed: tuple[Performance, ...] | None = None,
    selected: bool = True,
) -> CandidateReport:
    """One candidate whose evidence every argument can bend, so each gate can be isolated."""
    from tradingagent.research.protocol import StabilityReport

    stability = StabilityReport(
        score=0.9,
        out_of_sample_retention=retention,
        parameter_dispersion=dispersion,
        profitable_regime_ratio=1.0,
        trades=40 if out_of_sample is None else out_of_sample.trades,
        fragile=False,
        reasons=(),
    )
    gates = CandidateGates(
        walk_forward=walk_forward if walk_forward is not None else walk_forward_outcome(),
        perturbed=perturbed if perturbed is not None else (steady(), steady()),
        monte_carlo=monte_carlo(probability),
        p_value=p_value,
        stressed=stressed if stressed is not None else traded(win=0.6, loss=-0.4),
        out_of_sample=out_of_sample,
    )
    return CandidateReport(
        market="frxXAUUSD",
        label="balanced-2R",
        train=steady() if train is None else train,
        validation=steady(per_trade=0.6),
        cost_net=traded() if cost_net is None else cost_net,
        stability_score=stability.score,
        fragile=stability.fragile,
        reasons=stability.reasons,
        selected=selected,
        stability_report=stability,
        parameters={"ema_fast": 20.0, "ema_slow": 50.0},
        gates=gates,
    )


def verdicts(report: CandidateReport) -> dict[ValidationStage, GateVerdict]:
    return {item.stage: item for item in candidate_gate_verdicts(report, THRESHOLDS)}


def test_every_candidate_carries_the_nine_gates_in_protocol_order() -> None:
    items = candidate_gate_verdicts(candidate(), THRESHOLDS)
    assert tuple(item.stage for item in items) == STAGES
    assert len(items) == 9


def test_the_paper_and_risk_gates_are_never_evaluated_by_a_backtest_campaign() -> None:
    items = verdicts(candidate())
    for stage in (ValidationStage.PAPER, ValidationStage.RISK):
        verdict = items[stage]
        assert verdict.status is GateStatus.NOT_EVALUABLE
        assert verdict.evaluable is False
        # The reason has to say what would be needed, not merely that it is missing.
        assert len(verdict.reason) > 80
        assert verdict.threshold is None
    assert "thirty calendar" in items[ValidationStage.PAPER].reason
    assert "risk engine" in items[ValidationStage.RISK].reason


def test_the_backtest_gate_needs_enough_in_sample_trades() -> None:
    assert verdicts(candidate())[ValidationStage.BACKTEST].status is GateStatus.PASSED
    thin = verdicts(candidate(train=steady(trades=12)))
    assert thin[ValidationStage.BACKTEST].status is GateStatus.FAILED
    assert thin[ValidationStage.BACKTEST].reason == "12 in-sample trade(s), need 30"


def test_the_costs_gate_reads_the_charged_run_not_the_gross_one() -> None:
    # The gross run is a clean winner; the run that pays the charged costs is a loser.
    report = candidate(cost_net=losing(trades=60, per_trade=-0.5))
    assert verdicts(report)[ValidationStage.COSTS].status is GateStatus.FAILED
    assert verdicts(candidate())[ValidationStage.COSTS].status is GateStatus.PASSED


def test_the_walk_forward_gate_needs_a_majority_of_profitable_folds() -> None:
    passing = verdicts(candidate(walk_forward=walk_forward_outcome(profitable=4, folds=6)))
    assert passing[ValidationStage.WALK_FORWARD].status is GateStatus.PASSED
    failing = verdicts(candidate(walk_forward=walk_forward_outcome(profitable=2, folds=6)))
    assert failing[ValidationStage.WALK_FORWARD].status is GateStatus.FAILED
    assert "2/6 fold(s) profitable (33%)" in failing[ValidationStage.WALK_FORWARD].reason


def test_a_walk_forward_gate_with_no_played_fold_is_not_evaluable() -> None:
    # No fold fits, or every fold was too short to trade: nothing was measured, so nothing can
    # be claimed. A gate scored on an empty window would be a verdict on the window, not on
    # the rule.
    empty = WalkForwardOutcome(folds=0, profitable_folds=0, trades=0, net_profit=Decimal(0))
    items = verdicts(candidate(walk_forward=empty))
    assert items[ValidationStage.WALK_FORWARD].status is GateStatus.NOT_EVALUABLE
    assert "no walk-forward fold" in items[ValidationStage.WALK_FORWARD].reason
    skipped = WalkForwardOutcome(
        folds=0, profitable_folds=0, trades=0, net_profit=Decimal(0), skipped_folds=4
    )
    skipped_items = verdicts(candidate(walk_forward=skipped))
    assert skipped_items[ValidationStage.WALK_FORWARD].status is GateStatus.NOT_EVALUABLE
    assert skipped_items[ValidationStage.WALK_FORWARD].evidence["skipped_folds"] == 4
    assert "fewer bars than the manifest" in skipped_items[ValidationStage.WALK_FORWARD].reason


def test_the_out_of_sample_gate_is_not_evaluable_while_the_holdout_stays_sealed() -> None:
    items = verdicts(candidate(out_of_sample=None))
    verdict = items[ValidationStage.OUT_OF_SAMPLE]
    assert verdict.status is GateStatus.NOT_EVALUABLE
    assert "never unlocked" in verdict.reason
    assert verdict.evidence["holdout_read"] is False


def test_the_out_of_sample_gate_reads_the_holdout_once_it_was_read() -> None:
    items = verdicts(candidate(out_of_sample=losing(trades=40), retention=0.2))
    verdict = items[ValidationStage.OUT_OF_SAMPLE]
    assert verdict.status is GateStatus.FAILED
    assert "out-of-sample net profit" in verdict.reason
    assert verdict.evidence["holdout_read"] is True
    passed = verdicts(candidate(out_of_sample=steady(trades=40), retention=0.9))
    assert passed[ValidationStage.OUT_OF_SAMPLE].status is GateStatus.PASSED


def test_the_monte_carlo_gate_reads_the_resampled_probability_and_keeps_the_p_value() -> None:
    items = verdicts(candidate(probability=0.62, p_value=0.037))
    verdict = items[ValidationStage.MONTE_CARLO]
    assert verdict.status is GateStatus.PASSED
    assert verdict.evidence["probability_of_profit"] == 0.62
    assert verdict.evidence["p_value"] == 0.037
    weak = verdicts(candidate(probability=0.31))
    assert weak[ValidationStage.MONTE_CARLO].status is GateStatus.FAILED
    assert "0.31 below 0.50" in weak[ValidationStage.MONTE_CARLO].reason


def test_the_stress_gate_is_its_own_verdict_on_the_doubled_costs() -> None:
    # A rule that survives charged costs can still die at twice those costs.
    report = candidate(cost_net=traded(), stressed=losing(trades=40, per_trade=-0.2))
    assert verdicts(report)[ValidationStage.COSTS].status is GateStatus.PASSED
    stressed = verdicts(report)[ValidationStage.STRESS]
    assert stressed.status is GateStatus.FAILED
    assert stressed.evidence["cost_multiplier"] == 2.0
    assert "is not positive at 2x costs" in stressed.reason
    assert verdicts(candidate())[ValidationStage.STRESS].status is GateStatus.PASSED


def test_the_parameter_gate_reads_the_dispersion_of_the_perturbed_runs() -> None:
    wild = (steady(per_trade=5.0), losing(trades=40, per_trade=-5.0))
    items = verdicts(candidate(perturbed=wild))
    assert items[ValidationStage.PARAMETER_ROBUSTNESS].status is GateStatus.FAILED
    assert items[ValidationStage.PARAMETER_ROBUSTNESS].evidence["variants"] == 2
    assert verdicts(candidate())[ValidationStage.PARAMETER_ROBUSTNESS].status is GateStatus.PASSED


def test_each_verdict_names_the_function_that_produced_it() -> None:
    for item in candidate_gate_verdicts(candidate(), THRESHOLDS):
        assert item.evaluator.strip()
        assert item.reason.strip()
        if item.evaluable:
            assert item.evidence, item.stage
