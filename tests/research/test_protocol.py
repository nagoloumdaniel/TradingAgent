"""TASK-063: the holdout is sealed, optimisation cannot reach it, overfitting is flagged.

Hand rationale for the stability fixtures: an in-sample net profit of 470 EUR with 40 wins
of 12 EUR and 10 losses of 1 EUR is repeated out of sample as a loss of 175 EUR, so the
retention is 175/470 = -0.37: the candidate kept nothing it had found. A robust candidate
keeps 400/470 = 0.85 and its perturbed variants stay within a few percent.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradingagent.analytics.model import Performance, Trade
from tradingagent.analytics.performance import compute_performance
from tradingagent.backtest.datasets import CandleDataset, SyntheticRegime, synthetic_dataset
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.protocol import (
    PARAMETER_CV_MAX,
    DataSplit,
    Fold,
    SealedAccessError,
    SealedSet,
    WalkForwardPlan,
    confirm,
    monte_carlo,
    optimize,
    out_of_sample_retention,
    parameter_dispersion,
    period_report,
    perturb_parameters,
    split_dataset,
    stability_report,
    walk_forward,
)

START = datetime(2026, 1, 1, tzinfo=UTC)
GOLD = "frxXAUUSD"


def make_dataset(bars: int = 100):
    regimes = (SyntheticRegime(bars=bars, drift=0.0002, volatility=0.004),)
    return synthetic_dataset("gold-100", GOLD, Timeframe.M15, START, regimes, seed=11)


def trades_from(pnls, month: int = 1) -> list[Trade]:
    base = datetime(2026, month, 1, tzinfo=UTC)
    return [
        Trade(
            symbol=GOLD,
            strategy_ref="witness@1.0.0",
            direction=Direction.BUY if pnl >= 0 else Direction.SELL,
            timeframe=Timeframe.M15,
            mode=TradingMode.SIGNAL,
            opened_at=base + timedelta(minutes=index),
            closed_at=base + timedelta(minutes=index + 1),
            pnl_eur=Decimal(str(pnl)),
            risk_eur=Decimal("10"),
        )
        for index, pnl in enumerate(pnls)
    ]


def performance_of(pnls, month: int = 1) -> Performance:
    return compute_performance(trades_from(pnls, month))


def split_of(bars: int = 100) -> tuple[DataSplit, "CandleDataset"]:
    dataset = make_dataset(bars)
    return split_dataset(dataset, token="operator-2026"), dataset


def test_split_is_time_ordered() -> None:
    split, dataset = split_of()
    assert len(split.train) == 60
    assert len(split.validation) == 20
    assert split.holdout.size == 20
    assert split.train[0] == dataset.candles[0]
    assert split.validation[0] == dataset.candles[60]
    assert split.holdout.start == dataset.candles[80].open_time
    assert split.bars == 100


def test_split_refuses_fractions_that_leave_no_holdout() -> None:
    dataset = make_dataset(100)
    with pytest.raises(ValueError, match="holdout"):
        split_dataset(dataset, token="t", train_fraction=0.8, validation_fraction=0.2)


def test_holdout_cannot_be_read_without_the_token() -> None:
    split, _ = split_of()
    sealed = split.holdout
    assert sealed.size == 20
    with pytest.raises(SealedAccessError):
        _ = sealed.candles
    with pytest.raises(SealedAccessError):
        sealed.unlock("wrong-token")
    assert sealed.unlock_count == 0
    candles = sealed.unlock("operator-2026")
    assert len(candles) == 20
    assert sealed.unlock_count == 1
    assert "sealed=True" in repr(sealed)


def test_optimisation_that_touches_the_holdout_fails_technically() -> None:
    split, _ = split_of()
    runner = lambda parameters, candles: performance_of([1.0] * 40, month=3)  # noqa: E731
    with pytest.raises(SealedAccessError, match="holdout"):
        optimize(runner, [{"scale": 1.0}], split.train, split.holdout)  # type: ignore[arg-type]
    with pytest.raises(SealedAccessError):
        optimize(runner, [{"scale": 1.0}], split.holdout, split.validation)  # type: ignore[arg-type]


def test_a_seal_needs_a_token() -> None:
    with pytest.raises(ValueError, match="token"):
        SealedSet((), "")


def _robustness_runner(train_len: int, validation_len: int):
    def runner(parameters, candles):
        scale = float(parameters["scale"])
        if len(candles) == train_len:
            if scale == 2.0:
                return performance_of([scale * 5.0] * 39 + [-scale], month=1)
            return performance_of([scale * 3.0] * 39 + [-scale], month=1)
        if scale == 2.0:
            return performance_of([-2.0] * 30 + [1.0] * 10, month=2)
        return performance_of([1.5] * 30 + [-0.5] * 10, month=2)

    return runner


def test_selection_prefers_out_of_sample_robustness_over_training_profit() -> None:
    dataset = make_dataset(100)
    train = dataset.candles[:10]
    validation = dataset.candles[60:80]
    result = optimize(
        _robustness_runner(len(train), len(validation)),
        [{"scale": 1.0}, {"scale": 2.0}],
        train,
        validation,
    )
    tempting = result.scores[1]
    robust = result.scores[0]
    assert tempting.train.net_profit > robust.train.net_profit
    assert robust.validation.net_profit > tempting.validation.net_profit
    assert result.best_parameters["scale"] == 1.0
    assert result.best_index == 0


def test_confirm_is_the_single_audited_unlock() -> None:
    split, _ = split_of()
    runner = _robustness_runner(60, 20)
    with pytest.raises(SealedAccessError):
        confirm(runner, {"scale": 1.0}, split.holdout, "bad")
    performance = confirm(runner, {"scale": 1.0}, split.holdout, "operator-2026")
    assert isinstance(performance, Performance)
    assert split.holdout.unlock_count == 1


def test_perturbation_is_deterministic_and_brackets_every_parameter() -> None:
    variants = perturb_parameters({"ema_fast": 20, "stop": 1.5}, relative=0.1)
    assert len(variants) == 5
    assert variants[0] == {"ema_fast": 20, "stop": 1.5}
    assert {variant["ema_fast"] for variant in variants} == {18.0, 20.0, 22.0}
    assert {round(variant["stop"], 6) for variant in variants} == {1.35, 1.5, 1.65}
    with pytest.raises(ValueError):
        perturb_parameters({"a": 1.0}, relative=0)


def test_monte_carlo_is_reproducible() -> None:
    pnls = [1.0] * 20 + [-1.0] * 10
    first = monte_carlo(pnls, iterations=200, seed=42)
    second = monte_carlo(pnls, iterations=200, seed=42)
    other = monte_carlo(pnls, iterations=200, seed=43)
    assert first == second
    assert first != other
    assert 0.0 <= first.probability_of_profit <= 1.0
    assert first.net_profit_p05 <= first.net_profit_median <= first.net_profit_p95


def test_a_deliberately_overfitted_strategy_is_flagged_fragile() -> None:
    in_sample = performance_of([12.0] * 40 + [-1.0] * 10, month=1)
    out_of_sample = performance_of([-4.0] * 45 + [1.0] * 5, month=2)
    perturbations = [
        performance_of([12.0] * 40 + [-1.0] * 10, month=3),
        performance_of([-3.0] * 50, month=3),
        performance_of([2.0] * 50, month=3),
        performance_of([-1.0] * 50, month=3),
        performance_of([0.5] * 50, month=3),
    ]
    regimes = [
        performance_of([5.0] * 10, month=1),
        performance_of([-5.0] * 10, month=2),
        performance_of([-5.0] * 10, month=4),
    ]
    report = stability_report(
        in_sample, out_of_sample, perturbations=perturbations, regimes=regimes
    )
    assert report.fragile is True
    assert report.score < 0.5
    assert report.out_of_sample_retention < 0
    assert report.parameter_dispersion > PARAMETER_CV_MAX
    assert report.profitable_regime_ratio == pytest.approx(1 / 3)
    assert any("retention" in reason for reason in report.reasons)
    assert any("dispersion" in reason for reason in report.reasons)


def test_a_robust_strategy_is_not_flagged() -> None:
    in_sample = performance_of([12.0] * 40 + [-1.0] * 10, month=1)
    out_of_sample = performance_of([10.0] * 40 + [-1.0] * 10, month=2)
    perturbations = [
        performance_of([11.0] * 40 + [-1.0] * 10, month=3),
        performance_of([13.0] * 40 + [-1.0] * 10, month=3),
        performance_of([12.0] * 40 + [-1.5] * 10, month=3),
        performance_of([10.0] * 40 + [-0.5] * 10, month=3),
        performance_of([14.0] * 40 + [-1.0] * 10, month=3),
    ]
    regimes = [
        performance_of([8.0] * 10, month=1),
        performance_of([2.0] * 10, month=2),
        performance_of([5.0] * 10, month=4),
    ]
    report = stability_report(
        in_sample, out_of_sample, perturbations=perturbations, regimes=regimes
    )
    assert report.fragile is False
    assert report.reasons == ()
    assert report.out_of_sample_retention > 0.8
    assert report.parameter_dispersion < PARAMETER_CV_MAX


def test_a_too_small_sample_is_fragile_by_default() -> None:
    report = stability_report(performance_of([1.0] * 5), performance_of([1.0] * 5))
    assert report.fragile is True
    assert any("below 30" in reason for reason in report.reasons)


def test_walk_forward_rolls_the_origin_forward() -> None:
    dataset = make_dataset(100)
    plan = WalkForwardPlan(train_bars=30, validation_bars=10, step_bars=10)
    folds = walk_forward(dataset.candles, plan)
    assert [fold.index for fold in folds] == list(range(7))
    assert isinstance(folds[0], Fold)
    assert folds[0].train == dataset.candles[:30]
    assert folds[0].validation == dataset.candles[30:40]
    assert folds[-1].train == dataset.candles[60:90]
    assert folds[-1].validation == dataset.candles[90:100]


def test_walk_forward_can_be_capped() -> None:
    dataset = make_dataset(100)
    plan = WalkForwardPlan(train_bars=30, validation_bars=10, step_bars=10, max_folds=3)
    assert len(walk_forward(dataset.candles, plan)) == 3


def test_period_report_buckets_trades_by_month() -> None:
    trades = trades_from([5.0, -1.0], month=1) + trades_from([2.0], month=2)
    report = period_report(trades)
    assert [result.label for result in report] == ["2026-01", "2026-02"]
    assert report[0].performance.net_profit == Decimal("4")


def test_retention_and_dispersion_helpers() -> None:
    assert out_of_sample_retention(performance_of([10.0]), performance_of([5.0])) == 0.5
    assert out_of_sample_retention(performance_of([-10.0]), performance_of([5.0])) == 1.0
    assert out_of_sample_retention(performance_of([-10.0]), performance_of([-5.0])) == 0.0
    assert parameter_dispersion([]) == 0.0
    assert parameter_dispersion([performance_of([10.0])]) == 0.0
    assert parameter_dispersion([performance_of([9.0]), performance_of([11.0])]) < 0.11
