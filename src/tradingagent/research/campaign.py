"""Research campaigns across markets (F-025, TASK-064).

A campaign runs the same candidate set on several markets, compares them on identical
indicators, and selects on robustness rather than on net profit. Gold and crypto are
competing for the same risk budget, so the campaign also measures the correlation between
their returns: two highly correlated markets are one position in disguise.
"""

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from itertools import pairwise
from typing import Any

from tradingagent.analytics.model import Performance
from tradingagent.backtest.datasets import CandleDataset
from tradingagent.backtest.harness import BacktestConfig, run_backtest
from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.protocol import (
    StabilityReport,
    period_report,
    perturb_parameters,
    split_dataset,
    stability_report,
)
from tradingagent.strategies.base import Strategy
from tradingagent.strategies.manifest import StrategyManifest

DEFAULT_CORRELATION_THRESHOLD = 0.7
MIN_CORRELATION_BARS = 30


@dataclass(frozen=True)
class CandidateSpec:
    """One hypothesis: a strategy factory, a manifest and the parameters under study."""

    label: str
    manifest: StrategyManifest
    factory: Callable[[Mapping[str, float]], Strategy[Any]]
    parameters: Mapping[str, float]
    perturbation: float = 0.1


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


@dataclass(frozen=True)
class MarketReport:
    market: str
    dataset_id: str
    timeframe: Timeframe
    candidates: tuple[CandidateReport, ...]
    selected: str | None
    holdout_still_sealed: bool

    @property
    def selection_basis(self) -> str:
        return "stability score, then out-of-sample profit factor"


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

    def selected_by_market(self) -> dict[str, str]:
        return {
            market.market: market.selected for market in self.markets if market.selected is not None
        }

    def high_correlations(self) -> tuple[CorrelationPair, ...]:
        return tuple(pair for pair in self.correlations if pair.high)


def run_campaign(
    datasets: Mapping[str, CandleDataset],
    candidates: Sequence[CandidateSpec],
    *,
    config_for: Callable[[str, CandleDataset], BacktestConfig],
    train_fraction: float = 0.6,
    validation_fraction: float = 0.2,
    correlation_threshold: float = DEFAULT_CORRELATION_THRESHOLD,
) -> CampaignReport:
    """Evaluate every candidate on every market, then select on validated robustness."""
    if not datasets:
        raise ValueError("a campaign needs at least one market")
    if not candidates:
        raise ValueError("a campaign needs at least one candidate")
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
            )
        )
    return CampaignReport(
        markets=tuple(markets),
        correlations=correlate_markets(datasets, threshold=correlation_threshold),
    )


def _run_market(
    market: str,
    dataset: CandleDataset,
    candidates: Sequence[CandidateSpec],
    base_config: BacktestConfig,
    train_fraction: float,
    validation_fraction: float,
) -> MarketReport:
    split = split_dataset(
        dataset,
        token=f"campaign:{market}:holdout",
        train_fraction=train_fraction,
        validation_fraction=validation_fraction,
    )
    reports: list[CandidateReport] = []
    for candidate in candidates:
        reports.append(
            _run_candidate(market, dataset, candidate, base_config, split.train, split.validation)
        )
    ranked = rank_candidates(reports)
    selected = ranked[0].label if ranked else None
    marked = tuple(replace(report, selected=report.label == selected) for report in reports)
    return MarketReport(
        market=market,
        dataset_id=dataset.dataset_id,
        timeframe=dataset.timeframe,
        candidates=marked,
        selected=selected,
        holdout_still_sealed=split.holdout.unlock_count == 0,
    )


def _run_candidate(
    market: str,
    dataset: CandleDataset,
    candidate: CandidateSpec,
    base_config: BacktestConfig,
    train_candles: Sequence[Candle],
    validation_candles: Sequence[Candle],
) -> CandidateReport:
    base = candidate.factory(candidate.parameters)
    train_result = run_backtest(
        base, candidate.manifest, {dataset.timeframe: train_candles}, base_config
    )
    validation_result = run_backtest(
        base, candidate.manifest, {dataset.timeframe: validation_candles}, base_config
    )
    stressed = run_backtest(
        candidate.factory(candidate.parameters),
        candidate.manifest,
        {dataset.timeframe: validation_candles},
        replace(base_config, costs=base_config.costs.stressed(2.0)),
    )
    perturbations = [
        _validation_performance(candidate, dataset, parameters, base_config, validation_candles)
        for parameters in perturb_parameters(candidate.parameters, relative=candidate.perturbation)
    ]
    regimes = [regime.performance for regime in period_report(list(validation_result.trades))]
    stability = stability_report(
        train_result.performance,
        validation_result.performance,
        perturbations=perturbations,
        regimes=regimes,
    )
    return CandidateReport(
        market=market,
        label=candidate.label,
        train=train_result.performance,
        validation=validation_result.performance,
        cost_net=stressed.performance,
        stability_score=stability.score,
        fragile=stability.fragile,
        reasons=stability.reasons,
        selected=False,
        stability_report=stability,
        parameters=dict(candidate.parameters),
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
