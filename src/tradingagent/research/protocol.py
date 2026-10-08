"""Anti-overfitting protocol (F-025, R-02, TASK-063).

Overfitting is made *structurally* hard: the out-of-sample set is sealed behind an unlock
token, so no optimisation routine can read it by accident, and the tools that could try
only receive plain candle tuples. A stability report replaces the single profit figure: a
strategy that only shines on its training window is reported as fragile.

Two things were missing from that protocol and live here now, because a selection that tries
many candidates must be able to price its own luck:

* `monte_carlo_p_value` gives each survivor the probability that a rule *without* directional
  edge would have done as well, and `price_false_discoveries` applies the Benjamini-Hochberg
  correction over every attempt the selection made -- every (candidate, market) pair is one
  test, and an attempt that produced no p-value is counted as ``p = 1``, never dropped;
* `GateVerdict` and `GateStatus` carry the verdict of one gate of §49 together with the
  function that measured it, the figures behind it and the threshold it was compared against,
  so `NOT_EVALUABLE` is reportable instead of being silently read as a pass.

Statistics are separated from verdicts on purpose: this module measures, `promotion` decides,
and neither knows the other's policy.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from math import inf
from typing import Any

from tradingagent.analytics.axes import Axis, group
from tradingagent.analytics.model import Performance, Trade
from tradingagent.analytics.performance import compute_performance
from tradingagent.backtest.datasets import CandleDataset
from tradingagent.backtest.randomness import DeterministicRandom, percentile, standard_deviation
from tradingagent.core.market import Candle
from tradingagent.core.states import ValidationStage

OOS_RETENTION_MIN = 0.5
PARAMETER_CV_MAX = 0.5
PROFITABLE_REGIME_MIN = 0.5
STABILITY_SCORE_MIN = 0.5
MIN_TRADES_FOR_STABILITY = 30
#: Tolerated proportion of false discoveries among the candidates a selection retains. 0.10 is
#: the usual convention and it is *not* sacred: every report prints it next to its numbers.
DEFAULT_FALSE_DISCOVERY_RATE = 0.10
#: Draws of the sign-flip null used to estimate one candidate's p-value.
DEFAULT_MONTE_CARLO_ITERATIONS = 1_000


class SealedAccessError(RuntimeError):
    """An attempt to read the out-of-sample set without its unlock token."""


class SealedSet:
    """Candles the optimiser can measure but never read.

    `size` is public so a report can describe the set; the candles themselves require the
    token given at seal time. Every successful unlock is counted, which is the audit trail
    for "the holdout stayed sealed until the final confirmation".
    """

    __slots__ = ("_candles", "_token", "unlock_count")

    def __init__(self, candles: Sequence[Candle], token: str) -> None:
        if not token:
            raise ValueError("a seal token is required; an unlocked holdout is not a holdout")
        self._candles = tuple(candles)
        self._token = token
        self.unlock_count = 0

    @property
    def size(self) -> int:
        return len(self._candles)

    @property
    def start(self) -> datetime:
        return self._candles[0].open_time

    @property
    def end(self) -> datetime:
        return self._candles[-1].close_time

    @property
    def candles(self) -> tuple[Candle, ...]:
        raise SealedAccessError(
            "the out-of-sample set is sealed; call unlock(token) to confirm a candidate"
        )

    def __len__(self) -> int:
        return len(self._candles)

    def __repr__(self) -> str:
        return f"SealedSet(size={self.size}, sealed=True, unlocks={self.unlock_count})"

    def unlock(self, token: str) -> tuple[Candle, ...]:
        if token != self._token:
            raise SealedAccessError("wrong unlock token: the out-of-sample set stays sealed")
        self.unlock_count += 1
        return self._candles


@dataclass(frozen=True)
class DataSplit:
    """Time-ordered train / validation / sealed holdout."""

    train: tuple[Candle, ...]
    validation: tuple[Candle, ...]
    holdout: SealedSet

    @property
    def bars(self) -> int:
        return len(self.train) + len(self.validation) + self.holdout.size


def split_dataset(
    dataset: CandleDataset,
    *,
    token: str,
    train_fraction: float = 0.6,
    validation_fraction: float = 0.2,
) -> DataSplit:
    """Split by time, never by shuffling: a shuffled backtest leaks the future into the past."""
    if not 0 < train_fraction < 1:
        raise ValueError("train_fraction must be in (0, 1)")
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction must be in (0, 1)")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("train_fraction + validation_fraction must leave a holdout")
    count = len(dataset.candles)
    train_end = int(count * train_fraction)
    validation_end = train_end + int(count * validation_fraction)
    if train_end < 1 or validation_end <= train_end or validation_end >= count:
        raise ValueError(f"{count} bars cannot form train, validation and holdout")
    return DataSplit(
        train=dataset.candles[:train_end],
        validation=dataset.candles[train_end:validation_end],
        holdout=SealedSet(dataset.candles[validation_end:], token),
    )


@dataclass(frozen=True)
class WalkForwardPlan:
    train_bars: int
    validation_bars: int
    step_bars: int
    max_folds: int | None = None

    def __post_init__(self) -> None:
        if min(self.train_bars, self.validation_bars, self.step_bars) < 1:
            raise ValueError("walk-forward sizes must be positive")


@dataclass(frozen=True)
class Fold:
    index: int
    train: tuple[Candle, ...]
    validation: tuple[Candle, ...]


def walk_forward(candles: Sequence[Candle], plan: WalkForwardPlan) -> list[Fold]:
    """Rolling origin evaluation: retrain, validate on the next unseen block, step forward."""
    size = plan.train_bars + plan.validation_bars
    folds: list[Fold] = []
    start = 0
    index = 0
    while start + size <= len(candles):
        if plan.max_folds is not None and index >= plan.max_folds:
            break
        folds.append(
            Fold(
                index=index,
                train=tuple(candles[start : start + plan.train_bars]),
                validation=tuple(candles[start + plan.train_bars : start + size]),
            )
        )
        start += plan.step_bars
        index += 1
    return folds


@dataclass(frozen=True)
class WalkForwardOutcome:
    """How a candidate behaved on the rolling origin, folds included.

    A campaign and the discovery laboratory judge the same kind of evidence, so they share the
    shape of it. ``ratio`` -- the share of *played* folds that were profitable -- is what the
    walk-forward gate reads; it is deliberately not a profit figure, and a candidate with no
    fold at all has a ratio of ``0.0`` rather than a missing value.
    """

    folds: int
    profitable_folds: int
    trades: int
    net_profit: Decimal
    skipped_folds: int = 0

    @property
    def ratio(self) -> float:
        """The share of *played* folds that were profitable.

        ``folds`` counts the folds that actually ran, so a fold the manifest could not trade is
        excluded here rather than scored as a loss, and it is reported in ``skipped_folds``.
        """
        return self.profitable_folds / self.folds if self.folds else 0.0

    @property
    def testable(self) -> bool:
        """Whether at least one fold was actually evaluated on this evidence."""
        return self.folds > 0


@dataclass(frozen=True)
class CandidateScore:
    index: int
    parameters: Mapping[str, float]
    train: Performance
    validation: Performance
    score: float


@dataclass(frozen=True)
class OptimizationResult:
    best_parameters: Mapping[str, float]
    best_index: int
    scores: tuple[CandidateScore, ...]


def optimize(
    runner: Any,
    candidates: Sequence[Mapping[str, float]],
    train: Sequence[Candle],
    validation: Sequence[Candle],
) -> OptimizationResult:
    """Search parameters on train/validation only. A sealed set here is a hard error."""
    _reject_sealed(train, "train")
    _reject_sealed(validation, "validation")
    if not candidates:
        raise ValueError("no candidate to optimise")
    scores: list[CandidateScore] = []
    for index, parameters in enumerate(candidates):
        train_performance = runner(parameters, train)
        validation_performance = runner(parameters, validation)
        scores.append(
            CandidateScore(
                index=index,
                parameters=dict(parameters),
                train=train_performance,
                validation=validation_performance,
                score=robustness_score(train_performance, validation_performance),
            )
        )
    best = max(scores, key=lambda score: (score.score, -score.index))
    return OptimizationResult(
        best_parameters=best.parameters, best_index=best.index, scores=tuple(scores)
    )


def confirm(
    runner: Any,
    parameters: Mapping[str, float],
    holdout: SealedSet,
    token: str,
) -> Performance:
    """The single, explicit, audited read of the out-of-sample set."""
    return runner(parameters, holdout.unlock(token))


def _reject_sealed(value: Any, name: str) -> None:
    if isinstance(value, SealedSet):
        raise SealedAccessError(f"{name} is the sealed holdout; optimisation may not touch it")


def perturb_parameters(
    parameters: Mapping[str, float], *, relative: float = 0.1
) -> tuple[dict[str, float], ...]:
    """Base parameters first, then one deterministic nudge per numeric parameter."""
    if relative <= 0:
        raise ValueError("relative perturbation must be positive")
    variants: list[dict[str, float]] = [dict(parameters)]
    for key in sorted(parameters):
        value = parameters[key]
        if not isinstance(value, (int, float)):
            continue
        for sign in (1, -1):
            variant = dict(parameters)
            if isinstance(value, int):
                # Keep an integer parameter integral: a period of 4.5 is not a strategy.
                variant[key] = float(max(1, round(value * (1.0 + sign * relative))))
            else:
                variant[key] = float(value) * (1.0 + sign * relative)
            variants.append(variant)
    return tuple(variants)


@dataclass(frozen=True)
class MonteCarloReport:
    iterations: int
    trades: int
    net_profit_p05: float
    net_profit_median: float
    net_profit_p95: float
    probability_of_profit: float
    median_max_drawdown: float
    worst_max_drawdown: float


def monte_carlo(
    pnls: Sequence[float], *, iterations: int = 1_000, seed: int = 0
) -> MonteCarloReport:
    """Bootstrap the realized P&L: what a different ordering of the same trades could give."""
    if not pnls:
        raise ValueError("no realized P&L to resample")
    if iterations < 1:
        raise ValueError("iterations must be positive")
    stream = DeterministicRandom(seed)
    size = len(pnls)
    nets: list[float] = []
    drawdowns: list[float] = []
    profitable = 0
    for _ in range(iterations):
        sample = [pnls[stream.index(size)] for _ in range(size)]
        net = sum(sample)
        nets.append(net)
        drawdowns.append(_max_drawdown(sample))
        if net > 0:
            profitable += 1
    nets.sort()
    drawdowns.sort()
    return MonteCarloReport(
        iterations=iterations,
        trades=size,
        net_profit_p05=percentile(nets, 0.05),
        net_profit_median=percentile(nets, 0.5),
        net_profit_p95=percentile(nets, 0.95),
        probability_of_profit=profitable / iterations,
        median_max_drawdown=percentile(drawdowns, 0.5),
        worst_max_drawdown=drawdowns[-1],
    )


def _max_drawdown(pnls: Sequence[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


# --------------------------------------------------------------------------------------
# Significance and false-discovery control: a survivor of many attempts is not yet a
# discovery, and only a p-value can say how many attempts the correction must pay for.
# --------------------------------------------------------------------------------------
#
# The honest difficulty is that no other function of this module produces a p-value:
# `stability_report` is a robustness score, and `monte_carlo` resamples the realized P&L,
# which centres on the observed total and therefore answers "what other orderings of these
# same trades?" rather than "what would a rule without edge have done?". So the p-value is
# built here, explicitly, and its assumptions are written down rather than implied.


def monte_carlo_p_value(
    pnls: Sequence[float],
    *,
    iterations: int = DEFAULT_MONTE_CARLO_ITERATIONS,
    seed: int = 0,
) -> float:
    """The probability that a random draw *without edge* does at least as well.

    Null hypothesis: the entry rule carries no directional information on that tape. Under
    it the sign of each realized trade is as good as a fair coin -- the magnitudes are what
    the market and the costs gave, but the direction says nothing. The statistic is the total
    net profit; the null distribution is drawn by flipping the sign of every trade with an
    independent fair coin (a sign-flip randomization test, the Fisher test for paired
    observations), and the p-value is the share of those draws that reach the observed total.

    Two deliberate details:

    * the estimate is ``(count + 1) / (iterations + 1)``, never exactly zero: with
      ``iterations`` draws a Monte-Carlo p-value cannot resolve below ``1 / (iterations + 1)``,
      and reporting a zero would claim a precision this method does not have;
    * an empty sample returns ``1.0``: no trade is no evidence of an edge.

    What it does *not* do: the trades are treated as exchangeable, so clustered regimes and
    overlapping positions make the p-value optimistic, and the test only sees *directional*
    edge -- a rule whose gross edge is smaller than its costs shows a small total and lands
    near ``1.0``, which errs on the safe side.
    """
    if iterations < 1:
        raise ValueError("iterations must be positive")
    values = [float(value) for value in pnls]
    if not values:
        return 1.0
    observed = sum(values)
    stream = DeterministicRandom(seed)
    at_least_as_good = 0
    for _ in range(iterations):
        total = 0.0
        for value in values:
            total += value if stream.random() < 0.5 else -value
        if total >= observed:
            at_least_as_good += 1
    return (at_least_as_good + 1) / (iterations + 1)


def bonferroni_threshold(hypotheses: int, *, alpha: float = DEFAULT_FALSE_DISCOVERY_RATE) -> float:
    """The per-test threshold of the Bonferroni correction, shown only for comparison.

    Bonferroni divides the level by the number of tests and is therefore stricter than
    Benjamini-Hochberg whenever more than one test looks promising. It is reported so a
    reader can see how much of the result comes from the correction and how much from the
    choice of method; it never decides anything here. With no test at all, nothing has to be
    cleared, so the threshold is ``1.0``.
    """
    if hypotheses < 0:
        raise ValueError("hypotheses cannot be negative")
    if not 0 < alpha <= 1:
        raise ValueError("alpha must be in (0, 1]")
    return alpha / hypotheses if hypotheses else 1.0


def _benjamini_hochberg_cut_off(p_values: Sequence[float], alpha: float) -> float | None:
    """The p-value cut-off of the BH step-up: the largest ``p_(k)`` with ``p_(k) <= k/m*alpha``.

    ``None`` means "no rank qualifies", i.e. nothing is a discovery. Kept separate from the
    flags so the rule is written in one place and the decisions are a plain comparison.
    """
    ordered = sorted(p_values)
    cut_off: float | None = None
    for rank, value in enumerate(ordered, start=1):
        if value <= rank / len(ordered) * alpha:
            cut_off = value
    return cut_off


def benjamini_hochberg(
    p_values: Sequence[float], *, alpha: float = DEFAULT_FALSE_DISCOVERY_RATE
) -> tuple[bool, ...]:
    """Benjamini-Hochberg step-up: which hypotheses survive at a false-discovery rate alpha.

    Sort the p-values ascending; the largest rank ``k`` with ``p_(k) <= k / m * alpha`` fixes
    the cut-off, and every hypothesis with ``p <= p_(k)`` is rejected (i.e. *kept* here: a
    small p-value is what makes a candidate a discovery). With a single candidate the rule
    collapses to ``p <= alpha``, which is the honest reading of "no selection happened".

    Flags come back in the order the p-values were given, so a caller can zip them straight
    back onto its candidates.
    """
    if not 0 < alpha <= 1:
        raise ValueError("alpha must be in (0, 1]")
    for value in p_values:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"p-values must lie in [0, 1], got {value}")
    if not p_values:
        return ()
    cut_off = _benjamini_hochberg_cut_off(p_values, alpha)
    if cut_off is None:
        return tuple(False for _ in p_values)
    return tuple(value <= cut_off for value in p_values)


@dataclass(frozen=True)
class MultipleTestingReport:
    """What a false-discovery control did to one selection.

    ``hypotheses`` is the number of candidates that were tried, not the number that reached
    the end of the protocol: that is the number of attempts the correction must pay for.
    ``expected_false_discoveries`` is ``hypotheses * alpha``, the count of candidates a naive
    per-test rule would wave through on luck alone; ``expected_false_discovery_rate`` is the
    ``alpha`` Benjamini-Hochberg actually bounds among the retained.
    """

    method: str
    alpha: float
    hypotheses: int
    discoveries_before: int
    discoveries_after: int
    rejected_by_correction: int
    bonferroni_threshold: float
    expected_false_discoveries: float

    @property
    def expected_false_discovery_rate(self) -> float:
        """The tolerated false-discovery rate: the guarantee Benjamini-Hochberg gives."""
        return self.alpha


@dataclass(frozen=True)
class MultipleTestingOutcome:
    """One (label, p-value) pair and whether the correction let it through.

    ``p_value`` is ``None`` when the candidate never produced one -- it never cleared the
    protocol, so it carries no out-of-sample evidence. ``significant`` is false in that case:
    a candidate without a measurement cannot be a discovery.
    """

    label: str
    p_value: float | None
    significant: bool
    rank: int
    cut_off: float


@dataclass(frozen=True)
class FalseDiscoveryControl:
    """The verdict of the control, one entry per hypothesis, plus the totals."""

    outcomes: tuple[MultipleTestingOutcome, ...]
    report: MultipleTestingReport
    alpha: float

    def demoted(self) -> tuple[MultipleTestingOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if not outcome.significant)


def price_false_discoveries(
    labelled_p_values: Sequence[tuple[str, float | None]],
    *,
    alpha: float = DEFAULT_FALSE_DISCOVERY_RATE,
) -> FalseDiscoveryControl:
    """Price every hypothesis of one selection and demote the ones the correction rejects.

    Every (candidate, market) evaluation is one test. A candidate that never produced a
    p-value is counted with ``p = 1`` rather than dropped: the number of tests must equal the
    number of attempts the selection actually made, which makes the correction *conservative*
    rather than flattering. A demoted hypothesis keeps the p-value that demoted it and is told
    the threshold *its own rank* required, ``rank/m * alpha``: quoting another candidate's
    threshold would hide what it failed to clear.
    """
    count = len(labelled_p_values)
    raw = [1.0 if p_value is None else p_value for _, p_value in labelled_p_values]
    survives = benjamini_hochberg(raw, alpha=alpha)
    ranks = [1 + sum(1 for other in raw if other < value) for value in raw]
    outcomes = tuple(
        MultipleTestingOutcome(
            label=label,
            p_value=p_value,
            significant=bool(significant) and p_value is not None,
            rank=ranks[position],
            cut_off=ranks[position] / count * alpha if count else alpha,
        )
        for position, ((label, p_value), significant) in enumerate(
            zip(labelled_p_values, survives, strict=True)
        )
    )
    accepted = sum(1 for outcome in outcomes if outcome.significant)
    return FalseDiscoveryControl(
        outcomes=outcomes,
        report=MultipleTestingReport(
            method="benjamini_hochberg",
            alpha=alpha,
            hypotheses=count,
            # A hypothesis with no p-value cannot be a discovery, so the number of survivors
            # before the correction is the number of measured ones the rule did not demote.
            discoveries_before=sum(1 for _, p_value in labelled_p_values if p_value is not None),
            discoveries_after=accepted,
            rejected_by_correction=count
            - accepted
            - sum(1 for _, p_value in labelled_p_values if p_value is None),
            bonferroni_threshold=bonferroni_threshold(count, alpha=alpha),
            expected_false_discoveries=count * alpha,
        ),
        alpha=alpha,
    )


# --------------------------------------------------------------------------------------
# Promotion gates: which of the nine a measured campaign can decide, and on what number.
# --------------------------------------------------------------------------------------


class GateStatus(StrEnum):
    """The verdict of one gate of §49. ``NOT_EVALUABLE`` is a first-class result.

    There is deliberately no "probably fine": a gate the available evidence cannot decide is
    reported as such, with the reason and with what it would take to decide it. A gate
    "passed" without proof would be the worst possible outcome of a validation campaign.
    """

    PASSED = "passed"
    FAILED = "failed"
    NOT_EVALUABLE = "not_evaluable"

    @property
    def decided(self) -> bool:
        return self is not GateStatus.NOT_EVALUABLE


@dataclass(frozen=True)
class GateVerdict:
    """One gate of §49, the function that measured it, and the numbers behind the verdict.

    ``evidence`` maps a figure name to its value, always JSON-ready. ``threshold`` is the
    frozen threshold the figure was compared against; ``None`` means this module invented no
    threshold and the verdict rests on a sign or an existence, which is stated in ``reason``.
    """

    stage: ValidationStage
    status: GateStatus
    evaluator: str
    reason: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    threshold: float | None = None

    @property
    def passed(self) -> bool:
        return self.status is GateStatus.PASSED

    @property
    def evaluable(self) -> bool:
        return self.status.decided


def not_evaluable(stage: ValidationStage, reason: str) -> GateVerdict:
    """A gate the caller cannot measure, named with the evidence it would need."""
    return GateVerdict(
        stage=stage,
        status=GateStatus.NOT_EVALUABLE,
        evaluator="none: this evidence cannot be produced by a backtest campaign",
        reason=reason,
    )


@dataclass(frozen=True)
class RegimeResult:
    label: str
    performance: Performance


def period_report(trades: Sequence[Trade], axis: Axis = Axis.MONTH) -> tuple[RegimeResult, ...]:
    buckets = group(trades, axis)
    return tuple(
        RegimeResult(label=label, performance=compute_performance(items))
        for label, items in sorted(buckets.items())
    )


@dataclass(frozen=True)
class StabilityReport:
    """A robustness indicator, deliberately not a profit figure (TASK-063)."""

    score: float
    out_of_sample_retention: float
    parameter_dispersion: float
    profitable_regime_ratio: float
    trades: int
    fragile: bool
    reasons: tuple[str, ...]


def stability_report(
    in_sample: Performance,
    out_of_sample: Performance,
    *,
    perturbations: Sequence[Performance] = (),
    regimes: Sequence[Performance] = (),
    min_trades: int = MIN_TRADES_FOR_STABILITY,
) -> StabilityReport:
    retention = out_of_sample_retention(in_sample, out_of_sample)
    dispersion = parameter_dispersion(perturbations)
    regime_ratio = profitable_regime_ratio(regimes)
    score = stability_score(retention, dispersion, regime_ratio)
    reasons: list[str] = []
    if in_sample.trades < min_trades:
        reasons.append(f"only {in_sample.trades} in-sample trade(s), below {min_trades}")
    if retention < OOS_RETENTION_MIN:
        reasons.append(f"out-of-sample retention {retention:.2f} below {OOS_RETENTION_MIN:.2f}")
    if dispersion > PARAMETER_CV_MAX:
        reasons.append(f"parameter dispersion {dispersion:.2f} above {PARAMETER_CV_MAX:.2f}")
    if regime_ratio < PROFITABLE_REGIME_MIN:
        reasons.append(
            f"only {regime_ratio:.0%} of periods profitable, below {PROFITABLE_REGIME_MIN:.0%}"
        )
    if score < STABILITY_SCORE_MIN:
        reasons.append(f"stability score {score:.2f} below {STABILITY_SCORE_MIN:.2f}")
    return StabilityReport(
        score=score,
        out_of_sample_retention=retention,
        parameter_dispersion=dispersion,
        profitable_regime_ratio=regime_ratio,
        trades=out_of_sample.trades,
        fragile=bool(reasons),
        reasons=tuple(reasons),
    )


def out_of_sample_retention(in_sample: Performance, out_of_sample: Performance) -> float:
    gross = float(in_sample.net_profit)
    if gross > 0:
        return float(out_of_sample.net_profit) / gross
    return 1.0 if out_of_sample.net_profit > 0 else 0.0


def parameter_dispersion(perturbations: Sequence[Performance]) -> float:
    """Coefficient of variation of the net profit across perturbed parameters."""
    if len(perturbations) < 2:
        return 0.0
    profits = [float(performance.net_profit) for performance in perturbations]
    mean = sum(profits) / len(profits)
    if mean == 0:
        return inf if any(profit != 0 for profit in profits) else 0.0
    return standard_deviation(profits) / abs(mean)


def profitable_regime_ratio(regimes: Sequence[Performance]) -> float:
    if not regimes:
        return 1.0
    winners = sum(1 for performance in regimes if performance.net_profit > 0)
    return winners / len(regimes)


def robustness_score(train: Performance, validation: Performance) -> float:
    """Selection score used by `optimize`: retention first, profit factor second."""
    retention = _clamp01(out_of_sample_retention(train, validation))
    factor = validation.profit_factor or 0.0
    factor_component = _clamp01((factor - 1.0) / 2.0)
    return 0.6 * retention + 0.4 * factor_component


def stability_score(retention: float, dispersion: float, regime_ratio: float) -> float:
    retention_component = _clamp01(retention)
    dispersion_component = 1.0 - _clamp01(dispersion / PARAMETER_CV_MAX)
    regime_component = _clamp01(regime_ratio)
    return _clamp01(0.5 * retention_component + 0.3 * dispersion_component + 0.2 * regime_component)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
