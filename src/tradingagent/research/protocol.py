"""Anti-overfitting protocol (F-025, R-02, TASK-063).

Overfitting is made *structurally* hard: the out-of-sample set is sealed behind an unlock
token, so no optimisation routine can read it by accident, and the tools that could try
only receive plain candle tuples. A stability report replaces the single profit figure: a
strategy that only shines on its training window is reported as fragile.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import inf
from typing import Any

from tradingagent.analytics.axes import Axis, group
from tradingagent.analytics.model import Performance, Trade
from tradingagent.analytics.performance import compute_performance
from tradingagent.backtest.datasets import CandleDataset
from tradingagent.backtest.randomness import DeterministicRandom, percentile, standard_deviation
from tradingagent.core.market import Candle

OOS_RETENTION_MIN = 0.5
PARAMETER_CV_MAX = 0.5
PROFITABLE_REGIME_MIN = 0.5
STABILITY_SCORE_MIN = 0.5
MIN_TRADES_FOR_STABILITY = 30


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
