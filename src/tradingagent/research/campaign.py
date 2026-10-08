"""Research campaigns across markets (F-025, TASK-064, TASK-066).

A campaign runs the same candidate set on several markets, compares them on identical
indicators, and selects on robustness rather than on net profit. Gold and crypto are
competing for the same risk budget, so the campaign also measures the correlation between
their returns: two highly correlated markets are one position in disguise.

Since TASK-066 a campaign also *validates*. Every candidate of every market is measured on
every gate of §49 a frozen history can decide -- the backtest itself, its costs, the
rolling-origin walk-forward, a Monte-Carlo resampling and its sign-flip p-value, a
deliberately stressed cost model, and the parametric dispersion -- and the sealed
out-of-sample set is unlocked exactly once, for the candidate each market selected. The
verdicts travel with the report as `GateVerdict` values, `NOT_EVALUABLE` included: nothing
that was not measured can be read as a pass. The two gates that need live evidence (a paper
campaign of thirty calendar days, and risk-engine decisions on real signals) are named,
reasoned and left open. This module never invents their numbers.

Three properties are load-bearing and each has a test:

* the p-value of every candidate is measured *before* any holdout is opened and feeds
  `promotion.evidence_from_campaign`, so the promotion control is never skipped for lack of
  a number;
* the false-discovery correction is applied over every (candidate, market) pair the campaign
  tried, and a candidate that was demoted cannot keep a passed gate;
* selection reads no holdout: the split is built, the candidates are ranked, and only then is
  the winner's sealed window unlocked -- once per market, and only when the caller asks.
"""

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from itertools import pairwise
from typing import Any

from tradingagent.analytics.model import Performance
from tradingagent.backtest.datasets import CandleDataset
from tradingagent.backtest.harness import BacktestConfig, run_backtest
from tradingagent.core.market import Candle
from tradingagent.core.states import ValidationStage
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.discovery import _candidate_seed
from tradingagent.research.promotion import AcceptanceThresholds
from tradingagent.research.protocol import (
    DEFAULT_FALSE_DISCOVERY_RATE,
    DEFAULT_MONTE_CARLO_ITERATIONS,
    GateStatus,
    GateVerdict,
    MonteCarloReport,
    MultipleTestingReport,
    StabilityReport,
    WalkForwardOutcome,
    WalkForwardPlan,
    confirm,
    monte_carlo,
    monte_carlo_p_value,
    not_evaluable,
    parameter_dispersion,
    period_report,
    perturb_parameters,
    price_false_discoveries,
    split_dataset,
    stability_report,
    walk_forward,
)
from tradingagent.strategies.base import Strategy
from tradingagent.strategies.manifest import StrategyManifest

DEFAULT_CORRELATION_THRESHOLD = 0.7
MIN_CORRELATION_BARS = 30
#: The share of walk-forward folds that must be profitable. It is the default of the discovery
#: protocol (`DiscoveryProtocol.min_walk_forward_ratio`), reused rather than restated: a
#: campaign and a discovery run must not judge the same folds against different bars.
MIN_WALK_FORWARD_RATIO = 0.5
#: The walk-forward plan a campaign uses unless told otherwise. The validation block is
#: deliberately larger than the discovery laboratory's 100 bars: a block must first feed the
#: indicators their recursive warm-up and the manifest its declared history before it can place
#: a single trade. At 100 bars the reference strategy opens nothing at all, and a gate that
#: scores six empty folds as six losses is not reporting anything about the rule -- it is
#: reporting the size of its window. 250 bars is the smallest block measured to trade here.
DEFAULT_WALK_FORWARD_PLAN = WalkForwardPlan(
    train_bars=350, validation_bars=250, step_bars=200, max_folds=6
)
#: The cost multiplier of the `stress` gate. TASK-062 ships `CostModel.stressed` for exactly
#: this: the same rule replayed on execution twice as expensive as the one already charged.
STRESS_COST_MULTIPLIER = 2.0
#: TASK-071 / Q-15: the paper gate is a measurement in time, not a backtest figure.
PAPER_GATE_REQUIREMENT = (
    "the paper gate of TASK-071 requires a live paper campaign of at least thirty calendar "
    "days and thirty operations per strategy (Q-15): a backtest replays a frozen history and "
    "cannot produce elapsed real time, so any number produced here would not be a measurement "
    "of what the gate is about"
)
#: §49: the risk gate is about the production risk engine's verdicts on real signals.
RISK_GATE_REQUIREMENT = (
    "the risk gate requires the verdicts of the production risk engine on real signals "
    "(capital, margin, daily loss, exposure), recorded through "
    "StrategyRegistry.record_validation: a campaign observes a simulation with simulated "
    "sizing and no account, so measuring it here would grade a different object"
)


@dataclass(frozen=True)
class CandidateSpec:
    """One hypothesis: a strategy factory, a manifest and the parameters under study."""

    label: str
    manifest: StrategyManifest
    factory: Callable[[Mapping[str, float]], Strategy[Any]]
    parameters: Mapping[str, float]
    perturbation: float = 0.1


@dataclass(frozen=True)
class CandidateGates:
    """The offline evidence of one candidate, kept exactly as it was measured.

    Everything a `GateVerdict` cites lives here instead of being recomputed by the report, so
    a verdict and its numbers can never drift apart. ``out_of_sample`` is ``None`` for a
    candidate that was not selected: the sealed set is read for the winner of each market and
    for nobody else.
    """

    walk_forward: WalkForwardOutcome
    perturbed: tuple[Performance, ...]
    monte_carlo: MonteCarloReport
    p_value: float
    stressed: Performance
    out_of_sample: Performance | None = None
    #: Whether the Benjamini-Hochberg control let this candidate claim a discovery. It starts
    #: `True` -- pending the correction, the candidate cleared the protocol on its own -- and
    #: the correction has the last word: `apply_multiple_testing_to_markets` sets it.
    significant: bool = True

    @property
    def probability_of_profit(self) -> float:
        return self.monte_carlo.probability_of_profit


@dataclass(frozen=True)
class CandidateReport:
    market: str
    label: str
    train: Performance
    validation: Performance
    cost_net: Performance
    stability_score: float
    fragile: bool
    reasons: tuple[str, ...]
    selected: bool
    stability_report: StabilityReport | None = None
    parameters: Mapping[str, float] = field(default_factory=dict)
    gates: CandidateGates | None = None
    gate_verdicts: tuple[GateVerdict, ...] = ()

    def verdict(self, stage: ValidationStage) -> GateVerdict | None:
        return next((item for item in self.gate_verdicts if item.stage is stage), None)

    def passing_stages(self) -> tuple[ValidationStage, ...]:
        """The gates this candidate cleared, in protocol order."""
        cleared = {item.stage for item in self.gate_verdicts if item.passed}
        return tuple(stage for stage in ValidationStage if stage in cleared)


@dataclass(frozen=True)
class MarketReport:
    market: str
    dataset_id: str
    timeframe: Timeframe
    candidates: tuple[CandidateReport, ...]
    selected: str | None
    holdout_still_sealed: bool
    out_of_sample: Performance | None = None
    holdout_unlocks: int = 0
    gate_verdicts: tuple[GateVerdict, ...] = ()

    @property
    def selection_basis(self) -> str:
        return "stability score, then out-of-sample profit factor"

    @property
    def winner(self) -> CandidateReport | None:
        return next((item for item in self.candidates if item.selected), None)

    def verdict(self, stage: ValidationStage) -> GateVerdict | None:
        return next((item for item in self.gate_verdicts if item.stage is stage), None)

    def passing_stages(self) -> tuple[ValidationStage, ...]:
        cleared = {item.stage for item in self.gate_verdicts if item.passed}
        return tuple(stage for stage in ValidationStage if stage in cleared)


@dataclass(frozen=True)
class CorrelationPair:
    market_a: str
    market_b: str
    correlation: float | None
    aligned_bars: int
    threshold: float = DEFAULT_CORRELATION_THRESHOLD

    @property
    def high(self) -> bool:
        return self.correlation is not None and abs(self.correlation) >= self.threshold


@dataclass(frozen=True)
class CampaignReport:
    markets: tuple[MarketReport, ...]
    correlations: tuple[CorrelationPair, ...]
    gate_verdicts: tuple[GateVerdict, ...] = ()
    multiple_testing: MultipleTestingReport | None = None
    hypotheses: tuple[tuple[str, float | None], ...] = ()

    def claims(self) -> tuple[CandidateReport, ...]:
        """The candidates that cleared the protocol *and* the selection correction.

        This is the only set a promotion of this campaign may rest on: a candidate the
        correction demoted is not a discovery, however well its own window behaved.
        """
        return tuple(
            candidate
            for market in self.markets
            for candidate in market.candidates
            if candidate.gates is not None and candidate.gates.significant
        )

    def selected_by_market(self) -> dict[str, str]:
        return {
            market.market: market.selected for market in self.markets if market.selected is not None
        }

    def high_correlations(self) -> tuple[CorrelationPair, ...]:
        return tuple(pair for pair in self.correlations if pair.high)

    def verdict(self, stage: ValidationStage) -> GateVerdict | None:
        return next((item for item in self.gate_verdicts if item.stage is stage), None)

    def passing_stages(self) -> tuple[ValidationStage, ...]:
        """The gates every market cleared, in protocol order: what a promotion may claim."""
        cleared = {
            stage
            for stage in ValidationStage
            if self.markets and all(_cleared(market.verdict(stage)) for market in self.markets)
        }
        return tuple(stage for stage in ValidationStage if stage in cleared)

    def gate_summary(self) -> tuple[tuple[ValidationStage, GateStatus, float | None, str], ...]:
        """(stage, status, threshold, reason) for the nine gates, in protocol order."""
        return tuple(
            (
                item.stage,
                item.status,
                item.threshold,
                item.reason,
            )
            for item in self.gate_verdicts
        )


def run_campaign(
    datasets: Mapping[str, CandleDataset],
    candidates: Sequence[CandidateSpec],
    *,
    config_for: Callable[[str, CandleDataset], BacktestConfig],
    train_fraction: float = 0.6,
    validation_fraction: float = 0.2,
    correlation_threshold: float = DEFAULT_CORRELATION_THRESHOLD,
    thresholds: AcceptanceThresholds | None = None,
    false_discovery_rate: float = DEFAULT_FALSE_DISCOVERY_RATE,
    monte_carlo_iterations: int = DEFAULT_MONTE_CARLO_ITERATIONS,
    walk_forward_plan: WalkForwardPlan | None = None,
    confirm_holdout: bool = False,
) -> CampaignReport:
    """Evaluate every candidate on every market, then select, validate and price the selection.

    The campaign is offline: it decides the seven gates a frozen history can decide and
    reports the other two as not evaluable. ``confirm_holdout`` unlocks the sealed set of each
    market once, for the candidate that market selected; it is off unless asked, because
    reading a holdout is a deliberate act and never a side effect of running a report.
    """
    if not datasets:
        raise ValueError("a campaign needs at least one market")
    if not candidates:
        raise ValueError("a campaign needs at least one candidate")
    settings = thresholds if thresholds is not None else AcceptanceThresholds(version="campaign")
    plan = walk_forward_plan if walk_forward_plan is not None else DEFAULT_WALK_FORWARD_PLAN

    markets: list[MarketReport] = []
    for market, dataset in sorted(datasets.items()):
        markets.append(
            _run_market(
                market,
                dataset,
                candidates,
                config_for(market, dataset),
                train_fraction,
                validation_fraction,
                settings,
                plan,
                monte_carlo_iterations,
                confirm_holdout,
            )
        )

    # One test per (candidate, market) evaluation, priced *after* the holdouts were read: the
    # correction has to pay for every attempt the campaign actually made, not just for the
    # candidates that happened to survive to the end.
    hypotheses = tuple(
        (
            f"{market.market}:{candidate.label}",
            candidate.gates.p_value if candidate.gates is not None else None,
        )
        for market in markets
        for candidate in market.candidates
    )
    control = price_false_discoveries(hypotheses, alpha=false_discovery_rate)
    # The correction has the last word on the *claim*, not on the measurements: a candidate it
    # demoted is no longer a discovery (`significant = False`), and `CampaignReport.claims()`
    # can then never return it. The nine gates keep the figures they measured -- reopening a
    # costs gate because the selection was corrected would replace a measurement with a
    # verdict of another kind.
    adjusted = apply_multiple_testing_to_markets(markets, control.outcomes)
    return CampaignReport(
        markets=adjusted,
        correlations=correlate_markets(datasets, threshold=correlation_threshold),
        gate_verdicts=evaluate_campaign_gates(adjusted, settings),
        multiple_testing=control.report,
        hypotheses=hypotheses,
    )


def _run_market(
    market: str,
    dataset: CandleDataset,
    candidates: Sequence[CandidateSpec],
    base_config: BacktestConfig,
    train_fraction: float,
    validation_fraction: float,
    thresholds: AcceptanceThresholds,
    plan: WalkForwardPlan,
    monte_carlo_iterations: int,
    confirm_holdout: bool,
) -> MarketReport:
    token = f"campaign:{market}:holdout"
    split = split_dataset(
        dataset,
        token=token,
        train_fraction=train_fraction,
        validation_fraction=validation_fraction,
    )
    reports: list[CandidateReport] = []
    for candidate in candidates:
        reports.append(
            _run_candidate(
                market,
                dataset,
                candidate,
                base_config,
                split.train,
                split.validation,
                plan,
                monte_carlo_iterations,
            )
        )
    # Selection happens here and reads no holdout: the sealed set is still intact above.
    ranked = rank_candidates(reports)
    selected = ranked[0].label if ranked else None
    marked = tuple(replace(report, selected=report.label == selected) for report in reports)

    out_of_sample: Performance | None = None
    if confirm_holdout and selected is not None:
        winner = next(report for report in marked if report.selected)
        out_of_sample = _confirm_holdout(market, dataset, candidates, winner, base_config, split)
        marked = tuple(
            replace(
                report,
                gates=replace(report.gates, out_of_sample=out_of_sample)
                if report.selected and report.gates is not None
                else report.gates,
            )
            for report in marked
        )
    final = tuple(
        replace(report, gate_verdicts=candidate_gate_verdicts(report, thresholds))
        for report in marked
    )
    return MarketReport(
        market=market,
        dataset_id=dataset.dataset_id,
        timeframe=dataset.timeframe,
        candidates=final,
        selected=selected,
        holdout_still_sealed=out_of_sample is None,
        out_of_sample=out_of_sample,
        holdout_unlocks=split.holdout.unlock_count,
        gate_verdicts=rebuild_market_verdicts(final),
    )


def _confirm_holdout(
    market: str,
    dataset: CandleDataset,
    candidates: Sequence[CandidateSpec],
    winner: CandidateReport,
    base_config: BacktestConfig,
    split: Any,
) -> Performance:
    """The single, audited read of the holdout: the winner of one market, once."""
    spec = next(item for item in candidates if item.label == winner.label)
    timeframe = dataset.timeframe

    def runner(parameters: Mapping[str, float], candles: Sequence[Candle]) -> Performance:
        result = run_backtest(
            spec.factory(parameters), spec.manifest, {timeframe: candles}, base_config
        )
        return result.performance

    return confirm(runner, spec.parameters, split.holdout, f"campaign:{market}:holdout")


def _run_candidate(
    market: str,
    dataset: CandleDataset,
    candidate: CandidateSpec,
    base_config: BacktestConfig,
    train_candles: Sequence[Candle],
    validation_candles: Sequence[Candle],
    plan: WalkForwardPlan,
    monte_carlo_iterations: int,
) -> CandidateReport:
    train_result = run_backtest(
        candidate.factory(candidate.parameters),
        candidate.manifest,
        {dataset.timeframe: train_candles},
        base_config,
    )
    validation_result = run_backtest(
        candidate.factory(candidate.parameters),
        candidate.manifest,
        {dataset.timeframe: validation_candles},
        base_config,
    )
    stressed = run_backtest(
        candidate.factory(candidate.parameters),
        candidate.manifest,
        {dataset.timeframe: validation_candles},
        replace(base_config, costs=base_config.costs.stressed(STRESS_COST_MULTIPLIER)),
    )
    # `perturb_parameters` yields the base parameters first, so that backtest *is* the
    # validation run: recomputing it would only double the cost of the campaign.
    perturbations: list[Performance] = []
    for parameters in perturb_parameters(candidate.parameters, relative=candidate.perturbation):
        if parameters == dict(candidate.parameters):
            perturbations.append(validation_result.performance)
            continue
        perturbations.append(
            _validation_performance(candidate, dataset, parameters, base_config, validation_candles)
        )
    regimes = [regime.performance for regime in period_report(list(validation_result.trades))]
    stability = stability_report(
        train_result.performance,
        validation_result.performance,
        perturbations=perturbations,
        regimes=regimes,
    )
    folds, skipped_folds = _walk_forward_folds(
        candidate, dataset, base_config, tuple(train_candles) + tuple(validation_candles), plan
    )
    seed = _candidate_seed(market, "campaign", candidate.label)
    # The p-value and the probability of profit are measured on the validation window, before
    # any holdout is opened: a candidate priced on a set that its own selection already saw
    # would be graded on data it chose itself, and the correction would pay for a formality.
    pnls = [float(trade.pnl_eur) for trade in validation_result.trades]
    gates = CandidateGates(
        walk_forward=_walk_forward_outcome(folds, skipped_folds),
        perturbed=tuple(perturbations),
        monte_carlo=_monte_carlo_report(pnls, seed, monte_carlo_iterations),
        p_value=monte_carlo_p_value(pnls, iterations=monte_carlo_iterations, seed=seed),
        stressed=stressed.performance,
    )
    return CandidateReport(
        market=market,
        label=candidate.label,
        train=train_result.performance,
        validation=validation_result.performance,
        # `cost_net` has always meant "the validation window replayed with stressed costs":
        # the stress gate reads `gates.stressed`, this field keeps the published contract.
        cost_net=stressed.performance,
        stability_score=stability.score,
        fragile=stability.fragile,
        reasons=stability.reasons,
        selected=False,
        stability_report=stability,
        parameters=dict(candidate.parameters),
        gates=gates,
    )


def _monte_carlo_report(pnls: Sequence[float], seed: int, iterations: int) -> MonteCarloReport:
    """The resampling of the realized P&L, or a report that says there was nothing to draw.

    An empty sample is not a crash: a candidate that never traded is reported with a zero
    probability of profit, which fails the gate honestly, and its p-value stays at 1.0
    ("no trade is no evidence of an edge").
    """
    if not pnls:
        return MonteCarloReport(
            iterations=iterations,
            trades=0,
            net_profit_p05=0.0,
            net_profit_median=0.0,
            net_profit_p95=0.0,
            probability_of_profit=0.0,
            median_max_drawdown=0.0,
            worst_max_drawdown=0.0,
        )
    return monte_carlo(pnls, iterations=iterations, seed=seed)


def _walk_forward_folds(
    candidate: CandidateSpec,
    dataset: CandleDataset,
    base_config: BacktestConfig,
    rolling: Sequence[Candle],
    plan: WalkForwardPlan,
) -> tuple[tuple[Performance, ...], int]:
    """One validation block per fold, on the rolling origin of `protocol.walk_forward`.

    A block too short to feed the manifest's declared history is *skipped*, not scored: the
    harness refuses it with a `ValueError`, and counting that refusal as a losing fold would
    turn the size of the window into a verdict on the strategy. Skipped folds are counted
    separately, and `_walk_forward_gate` reports `NOT_EVALUABLE` when every fold was skipped.
    """
    performances: list[Performance] = []
    skipped = 0
    for fold in walk_forward(rolling, plan):
        try:
            result = run_backtest(
                candidate.factory(candidate.parameters),
                candidate.manifest,
                {dataset.timeframe: fold.validation},
                base_config,
            )
        except ValueError:
            skipped += 1
            continue
        performances.append(result.performance)
    return tuple(performances), skipped


def _walk_forward_outcome(
    folds: Sequence[Performance], skipped_folds: int = 0
) -> WalkForwardOutcome:
    profit = Decimal(0)
    for performance in folds:
        profit += performance.net_profit
    return WalkForwardOutcome(
        folds=len(folds),
        profitable_folds=sum(1 for performance in folds if performance.net_profit > 0),
        trades=sum(performance.trades for performance in folds),
        net_profit=profit,
        skipped_folds=skipped_folds,
    )


def _validation_performance(
    candidate: CandidateSpec,
    dataset: CandleDataset,
    parameters: Mapping[str, float],
    base_config: BacktestConfig,
    validation_candles: Sequence[Candle],
) -> Performance:
    result = run_backtest(
        candidate.factory(parameters),
        candidate.manifest,
        {dataset.timeframe: validation_candles},
        base_config,
    )
    return result.performance


# --------------------------------------------------------------------------------------
# The nine verdicts of one candidate, then of one market, then of the campaign.
# --------------------------------------------------------------------------------------


def candidate_gate_verdicts(
    report: CandidateReport, thresholds: AcceptanceThresholds
) -> tuple[GateVerdict, ...]:
    """The nine gates of §49 for one candidate, in protocol order, evidence included.

    Two of them cannot be decided here and say so. A gate that was not measured is never
    reported as passed: the evidence it would need is named instead.
    """
    gates = report.gates
    if gates is None:  # pragma: no cover - every campaign candidate carries its measurement
        empty = "this candidate carries no measurement, so no gate can be decided from it"
        return tuple(not_evaluable(stage, empty) for stage in ValidationStage)
    return (
        _backtest_gate(report, thresholds),
        _costs_gate(report, thresholds),
        _walk_forward_gate(gates, thresholds),
        _out_of_sample_gate(report, gates, thresholds),
        _monte_carlo_gate(gates, thresholds),
        _stress_gate(gates, thresholds),
        _parameter_robustness_gate(gates, thresholds),
        not_evaluable(ValidationStage.PAPER, PAPER_GATE_REQUIREMENT),
        not_evaluable(ValidationStage.RISK, RISK_GATE_REQUIREMENT),
    )


def market_gate_verdict(
    market: str,
    stage: ValidationStage,
    candidates: Sequence[CandidateReport],
) -> GateVerdict:
    """One market's verdict on one gate: the verdict of the candidate that market selected.

    An unselected candidate is still measured, and every figure of every candidate travels in
    the report, but a gate is a claim about *the* candidate a market would put forward: the
    winner decides the verdict, and the losers are named beside it rather than voting. Letting
    a candidate nobody selected fail a gate on the market's behalf would be as wrong as
    letting it pass one.
    """
    winner = next((item for item in candidates if item.selected), None)
    if winner is None:
        return not_evaluable(stage, f"{market} selected no candidate, so this gate has no subject")
    verdict = winner.verdict(stage)
    if verdict is None:  # pragma: no cover - every candidate carries nine verdicts
        return not_evaluable(stage, f"{market} produced no verdict for {stage.value}")
    also_failed = [
        item.label for item in candidates if not item.selected and not _cleared(item.verdict(stage))
    ]
    evaluator = " | ".join(dict.fromkeys(verdict.evaluator.split(" | ")))
    return replace(
        verdict,
        evaluator=evaluator,
        evidence={
            **dict(verdict.evidence),
            "market": market,
            "candidate": winner.label,
            "candidates_tried": len(candidates),
            # Context, not a vote: these candidates are not the one a promotion would cover.
            "other_candidates_that_failed_this_hard_gate": also_failed,
        },
    )


def _backtest_gate(report: CandidateReport, thresholds: AcceptanceThresholds) -> GateVerdict:
    trades = report.train.trades
    passed = trades >= thresholds.min_trades
    return GateVerdict(
        stage=ValidationStage.BACKTEST,
        status=GateStatus.PASSED if passed else GateStatus.FAILED,
        evaluator="campaign._run_candidate -> harness.run_backtest (in-sample window)",
        reason=(
            f"{trades} in-sample trade(s), need {thresholds.min_trades}"
            if not passed
            else f"{trades} in-sample trade(s) clear {thresholds.min_trades}"
        ),
        evidence={
            "in_sample_trades": trades,
            "in_sample_net_profit": str(report.train.net_profit),
            "in_sample_profit_factor": report.train.profit_factor,
        },
        threshold=float(thresholds.min_trades),
    )


def _costs_gate(report: CandidateReport, thresholds: AcceptanceThresholds) -> GateVerdict:
    factor = report.cost_net.profit_factor
    drawdown = report.cost_net.max_drawdown
    reasons: list[str] = []
    if factor is None or factor < thresholds.min_profit_factor_net:
        shown = "undefined" if factor is None else f"{factor:.2f}"
        reasons.append(f"net profit factor {shown} below {thresholds.min_profit_factor_net:.2f}")
    if drawdown > thresholds.max_drawdown_eur:
        reasons.append(f"drawdown {drawdown} above {thresholds.max_drawdown_eur}")
    return GateVerdict(
        stage=ValidationStage.COSTS,
        status=GateStatus.PASSED if not reasons else GateStatus.FAILED,
        evaluator="campaign._run_candidate -> run_backtest with the charged CostModel",
        reason="costs are survivable" if not reasons else "; ".join(reasons),
        evidence={
            "net_profit": str(report.cost_net.net_profit),
            "profit_factor": factor,
            "max_drawdown": str(drawdown),
            "trades": report.cost_net.trades,
            "measurement": "validation window with charged costs",
        },
        threshold=thresholds.min_profit_factor_net,
    )


def _walk_forward_gate(gates: CandidateGates, thresholds: AcceptanceThresholds) -> GateVerdict:
    del thresholds  # the walk-forward bar is the protocol's, not the promotion's
    outcome = gates.walk_forward
    if not outcome.testable:
        return GateVerdict(
            stage=ValidationStage.WALK_FORWARD,
            status=GateStatus.NOT_EVALUABLE,
            evaluator=("campaign._walk_forward_folds -> protocol.walk_forward (rolling origin)"),
            reason=(
                "no walk-forward fold could be played: "
                + (
                    f"all {outcome.skipped_folds} fold(s) held fewer bars than the manifest's "
                    "declared history"
                    if outcome.skipped_folds
                    else "no fold fits in the rolling window"
                )
            ),
            evidence={
                "folds": 0,
                "skipped_folds": outcome.skipped_folds,
                "ratio": None,
                "measurement": "rolling origin; no block could be evaluated",
            },
            threshold=MIN_WALK_FORWARD_RATIO,
        )
    passed = outcome.ratio >= MIN_WALK_FORWARD_RATIO
    if not passed:
        reason = (
            f"{outcome.profitable_folds}/{outcome.folds} fold(s) profitable "
            f"({outcome.ratio:.0%}) below {MIN_WALK_FORWARD_RATIO:.0%}"
        )
    else:
        reason = (
            f"{outcome.profitable_folds}/{outcome.folds} fold(s) profitable "
            f"({outcome.ratio:.0%}) at or above {MIN_WALK_FORWARD_RATIO:.0%}"
        )
    return GateVerdict(
        stage=ValidationStage.WALK_FORWARD,
        status=GateStatus.PASSED if passed else GateStatus.FAILED,
        evaluator="campaign._walk_forward_folds -> protocol.walk_forward (rolling origin)",
        reason=reason,
        evidence={
            "folds": outcome.folds,
            "profitable_folds": outcome.profitable_folds,
            "ratio": outcome.ratio,
            "trades": outcome.trades,
            "net_profit": str(outcome.net_profit),
            "skipped_folds": outcome.skipped_folds,
            "measurement": "validation blocks of the rolling origin, never the sealed set",
        },
        threshold=MIN_WALK_FORWARD_RATIO,
    )


def _out_of_sample_gate(
    report: CandidateReport, gates: CandidateGates, thresholds: AcceptanceThresholds
) -> GateVerdict:
    sample = gates.out_of_sample
    if sample is None:
        held = (
            "this candidate was not the one its market selected"
            if not report.selected
            else "the campaign was asked to keep the sealed set sealed"
        )
        return GateVerdict(
            stage=ValidationStage.OUT_OF_SAMPLE,
            status=GateStatus.NOT_EVALUABLE,
            evaluator=(
                "campaign._confirm_holdout -> protocol.confirm, called once per market for "
                "the selected candidate"
            ),
            reason=f"the sealed set was never unlocked: {held}",
            evidence={"selected": report.selected, "holdout_read": False},
        )
    if report.stability_report is None:  # pragma: no cover - stability is always measured
        return not_evaluable(ValidationStage.OUT_OF_SAMPLE, "no stability report to compare with")
    retention = report.stability_report.out_of_sample_retention
    trades = sample.trades
    reasons: list[str] = []
    if trades < thresholds.min_trades:
        reasons.append(f"{trades} out-of-sample trade(s), need {thresholds.min_trades}")
    if sample.net_profit <= 0:
        reasons.append(f"out-of-sample net profit {sample.net_profit} is not positive")
    if retention < thresholds.min_out_of_sample_retention:
        reasons.append(
            f"retention {retention:.2f} below {thresholds.min_out_of_sample_retention:.2f}"
        )
    return GateVerdict(
        stage=ValidationStage.OUT_OF_SAMPLE,
        status=GateStatus.PASSED if not reasons else GateStatus.FAILED,
        evaluator="campaign._confirm_holdout -> protocol.confirm (one audited unlock)",
        reason="the sealed set confirms the candidate" if not reasons else "; ".join(reasons),
        evidence={
            "net_profit": str(sample.net_profit),
            "profit_factor": sample.profit_factor,
            "trades": trades,
            "retention": retention,
            "holdout_read": True,
            "measurement": "sealed out-of-sample window, after selection",
        },
        threshold=thresholds.min_out_of_sample_retention,
    )


def _monte_carlo_gate(gates: CandidateGates, thresholds: AcceptanceThresholds) -> GateVerdict:
    probability = gates.probability_of_profit
    passed = probability >= thresholds.min_monte_carlo_probability
    return GateVerdict(
        stage=ValidationStage.MONTE_CARLO,
        status=GateStatus.PASSED if passed else GateStatus.FAILED,
        evaluator=(
            "campaign._run_candidate -> protocol.monte_carlo (bootstrap of the realized P&L) "
            "and protocol.monte_carlo_p_value (sign-flip null)"
        ),
        reason=(
            f"resampled probability of profit {probability:.2f} below "
            f"{thresholds.min_monte_carlo_probability:.2f}"
            if not passed
            else (
                f"resampled probability of profit {probability:.2f} clears "
                f"{thresholds.min_monte_carlo_probability:.2f}"
            )
        ),
        evidence={
            "probability_of_profit": probability,
            "p_value": gates.p_value,
            "iterations": gates.monte_carlo.iterations,
            "trades": gates.monte_carlo.trades,
            "net_profit_p05": gates.monte_carlo.net_profit_p05,
            "net_profit_median": gates.monte_carlo.net_profit_median,
            "worst_max_drawdown": gates.monte_carlo.worst_max_drawdown,
            "measurement": "validation window, measured before any holdout was opened",
        },
        threshold=thresholds.min_monte_carlo_probability,
    )


def _stress_gate(gates: CandidateGates, thresholds: AcceptanceThresholds) -> GateVerdict:
    stressed = gates.stressed
    factor = stressed.profit_factor
    reasons: list[str] = []
    if stressed.net_profit <= 0:
        reasons.append(
            f"net profit {stressed.net_profit} is not positive at {STRESS_COST_MULTIPLIER:g}x costs"
        )
    if factor is None or factor < thresholds.min_profit_factor_net:
        shown = "undefined" if factor is None else f"{factor:.2f}"
        reasons.append(
            f"net profit factor {shown} below {thresholds.min_profit_factor_net:.2f} at "
            f"{STRESS_COST_MULTIPLIER:g}x costs"
        )
    return GateVerdict(
        stage=ValidationStage.STRESS,
        status=GateStatus.PASSED if not reasons else GateStatus.FAILED,
        evaluator="campaign._run_candidate -> CostModel.stressed(2.0) replayed by run_backtest",
        reason="the rule survives doubled costs" if not reasons else "; ".join(reasons),
        evidence={
            "cost_multiplier": STRESS_COST_MULTIPLIER,
            "net_profit": str(stressed.net_profit),
            "profit_factor": factor,
            "trades": stressed.trades,
            "max_drawdown": str(stressed.max_drawdown),
            # Inventing a laxer bar for the stressed run would be lowering a threshold by the
            # back door: the rule must clear what it must clear at charged costs, but at two.
            "criteria_borrowed_from": ValidationStage.COSTS.value,
        },
        threshold=thresholds.min_profit_factor_net,
    )


def _parameter_robustness_gate(
    gates: CandidateGates, thresholds: AcceptanceThresholds
) -> GateVerdict:
    dispersion = parameter_dispersion(gates.perturbed)
    passed = dispersion <= thresholds.max_parameter_dispersion
    return GateVerdict(
        stage=ValidationStage.PARAMETER_ROBUSTNESS,
        status=GateStatus.PASSED if passed else GateStatus.FAILED,
        evaluator=(
            "campaign._run_candidate -> protocol.perturb_parameters (+-10%) then "
            "protocol.parameter_dispersion"
        ),
        reason=(
            f"parameter dispersion {dispersion:.2f} above {thresholds.max_parameter_dispersion:.2f}"
            if not passed
            else (
                f"parameter dispersion {dispersion:.2f} within "
                f"{thresholds.max_parameter_dispersion:.2f}"
            )
        ),
        evidence={
            "parameter_dispersion": dispersion,
            "variants": len(gates.perturbed),
            "net_profits": [str(item.net_profit) for item in gates.perturbed],
        },
        threshold=thresholds.max_parameter_dispersion,
    )


def evaluate_campaign_gates(
    markets: Sequence[MarketReport], thresholds: AcceptanceThresholds
) -> tuple[GateVerdict, ...]:
    """The nine gates of §49 for the whole campaign, in protocol order.

    A gate cleared by one market and failed by another is *failed* here: the campaign is the
    unit that asks for a promotion, so it may only claim what every market it covers can
    support. A gate no market could decide stays not evaluable, whatever the others said.
    """
    if not markets:
        return tuple(
            not_evaluable(stage, "the campaign covered no market") for stage in ValidationStage
        )
    verdicts: list[GateVerdict] = []
    for stage in ValidationStage:
        present = [market.verdict(stage) for market in markets]
        if any(verdict is None for verdict in present):  # pragma: no cover - nine per market
            verdicts.append(
                not_evaluable(stage, "at least one market produced no verdict for this gate")
            )
            continue
        decided = [verdict for verdict in present if verdict is not None]
        if any(verdict.status is GateStatus.NOT_EVALUABLE for verdict in decided):
            undecided = [
                market.market
                for market, verdict in zip(markets, decided, strict=True)
                if verdict.status is GateStatus.NOT_EVALUABLE
            ]
            verdicts.append(
                not_evaluable(
                    stage,
                    f"not evaluable for {', '.join(undecided)}: {decided[0].reason}",
                )
            )
            continue
        failed = [
            market.market
            for market, verdict in zip(markets, decided, strict=True)
            if not verdict.passed
        ]
        verdicts.append(
            GateVerdict(
                stage=stage,
                status=GateStatus.FAILED if failed else GateStatus.PASSED,
                evaluator=" | ".join(
                    dict.fromkeys(
                        part
                        for item in decided
                        for part in item.evaluator.split(" | ")
                        if part.strip()
                    )
                ),
                reason=(
                    f"{stage.value} failed on: {', '.join(failed)}"
                    if failed
                    else f"{stage.value} cleared on every market: "
                    + "; ".join(item.reason for item in decided)
                ),
                evidence={
                    "markets": {
                        market.market: dict(verdict.evidence)
                        for market, verdict in zip(markets, decided, strict=True)
                    },
                    "failed_markets": failed,
                },
                threshold=decided[0].threshold,
            )
        )
    return tuple(verdicts)


def rebuild_market_verdicts(candidates: Sequence[CandidateReport]) -> tuple[GateVerdict, ...]:
    """The nine market verdicts of one market, recomputed from its candidates' verdicts.

    One place builds them, so the first computation and the one that follows the
    false-discovery correction can never drift apart.
    """
    return tuple(
        market_gate_verdict(candidate_market(candidates), stage, candidates)
        for stage in ValidationStage
    )


def candidate_market(candidates: Sequence[CandidateReport]) -> str:
    return candidates[0].market if candidates else "unknown"


def apply_multiple_testing_to_markets(
    markets: Sequence[MarketReport], outcomes: Sequence[Any]
) -> tuple[MarketReport, ...]:
    """Record the correction's verdict on every candidate of the selection.

    A candidate the Benjamini-Hochberg rule demoted keeps its measured p-value, its figures
    and its nine gate verdicts -- those are facts about a backtest -- and loses its *claim*:
    `CandidateGates.significant` becomes false, so `CampaignReport.claims()` stops returning
    it and no promotion can rest on it. Reopening the gates instead would replace a
    measurement (this run traded at these costs) with a verdict of another kind, and would
    turn a corrected selection into a report full of falsified readings.
    """
    demoted = {position for position, outcome in enumerate(outcomes) if not outcome.significant}
    position = 0
    adjusted: list[MarketReport] = []
    for market in markets:
        candidates: list[CandidateReport] = []
        for candidate in market.candidates:
            is_demoted = position in demoted
            position += 1
            if not is_demoted or candidate.gates is None:
                candidates.append(candidate)
                continue
            candidates.append(replace(candidate, gates=replace(candidate.gates, significant=False)))
        adjusted.append(replace(market, candidates=tuple(candidates)))
    return tuple(adjusted)


def _cleared(verdict: GateVerdict | None) -> bool:
    """True only for a gate that was measured *and* passed.

    `None` and `NOT_EVALUABLE` are both "not cleared": a gate nobody could decide must never
    be read as a pass.
    """
    return verdict is not None and verdict.passed


def rank_candidates(
    reports: Sequence[CandidateReport],
) -> tuple[CandidateReport, ...]:
    """Robustness first, profit factor only as a tie-break: never raw net profit."""
    return tuple(
        sorted(
            reports,
            key=lambda report: (
                -report.stability_score,
                -(report.validation.profit_factor or 0.0),
                report.label,
            ),
        )
    )


def correlate_markets(
    datasets: Mapping[str, CandleDataset],
    *,
    threshold: float = DEFAULT_CORRELATION_THRESHOLD,
) -> tuple[CorrelationPair, ...]:
    """Pearson correlation of aligned simple returns for every market pair."""
    names = sorted(datasets)
    pairs: list[CorrelationPair] = []
    for index, first in enumerate(names):
        for second in names[index + 1 :]:
            returns_a, returns_b = _aligned_returns(datasets[first], datasets[second])
            value = pearson(returns_a, returns_b)
            pairs.append(
                CorrelationPair(
                    market_a=first,
                    market_b=second,
                    correlation=value,
                    aligned_bars=len(returns_a),
                    threshold=threshold,
                )
            )
    return tuple(pairs)


def _aligned_returns(
    first: CandleDataset, second: CandleDataset
) -> tuple[list[float], list[float]]:
    closings_b = {candle.open_time: candle.close for candle in second.candles}
    left: list[float] = []
    right: list[float] = []
    shared = [candle for candle in first.candles if candle.open_time in closings_b]
    for earlier, later in pairwise(shared):
        previous = closings_b[earlier.open_time]
        current = closings_b[later.open_time]
        if previous == 0 or earlier.close == 0:
            continue
        left.append((later.close - earlier.close) / earlier.close)
        right.append((current - previous) / previous)
    return left, right


def pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right):
        raise ValueError("correlation series must have the same length")
    if len(left) < MIN_CORRELATION_BARS:
        return None
    mean_left = sum(left) / len(left)
    mean_right = sum(right) / len(right)
    covariance = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right, strict=True))
    variance_left = sum((a - mean_left) ** 2 for a in left)
    variance_right = sum((b - mean_right) ** 2 for b in right)
    if variance_left <= 0 or variance_right <= 0:
        return None
    return max(-1.0, min(1.0, covariance / math.sqrt(variance_left * variance_right)))
