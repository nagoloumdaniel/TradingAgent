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
from tradingagent.backtest.randomness import DeterministicRandom
from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.protocol import (
    PARAMETER_CV_MAX,
    DataSplit,
    DataWindow,
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


def prepend_bars(dataset: CandleDataset, bars: int) -> CandleDataset:
    """The same series with `bars` older candles in front of it, on the same M15 grid.

    Older history is exactly what a longer fetch produces, and it is the operation the
    anchored split exists for: prepending must not move the newest windows. The prepended
    block is *built*, not generated, so the concatenation stays a gap-free M15 series -- the
    market calendar is not what these boundary tests are about.
    """
    first = dataset.candles[0]
    stream = DeterministicRandom(2026)
    older = [stream.gauss() for _ in range(bars)]
    step = timedelta(seconds=dataset.timeframe.seconds)
    scale = first.open
    # The last prepended candle closes exactly where the original series opens, so the
    # concatenation is one gap-free M15 series.
    candles = tuple(
        Candle(
            timeframe=dataset.timeframe,
            open_time=first.open_time - step * (bars - index),
            open=scale * (1.0 + move * 0.001),
            high=scale * (1.0 + move * 0.001 + 0.0005),
            low=scale * (1.0 + move * 0.001 - 0.0005),
            close=scale * (1.0 + move * 0.001),
        )
        for index, move in enumerate(older)
    )
    return CandleDataset(
        dataset_id=f"{dataset.dataset_id}+{bars}-prepended",
        symbol=dataset.symbol,
        timeframe=dataset.timeframe,
        source=f"test:prepended+{bars}",
        candles=candles + tuple(dataset.candles),
    )


def make_tiny_dataset(bars: int) -> CandleDataset:
    """A hand-built series, so a boundary case does not depend on a generator's minimum."""
    candles = tuple(
        Candle(
            timeframe=Timeframe.M15,
            open_time=START + timedelta(minutes=15 * index),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
        )
        for index in range(bars)
    )
    return CandleDataset(
        dataset_id=f"tiny-{bars}",
        symbol=GOLD,
        timeframe=Timeframe.M15,
        source=f"test:tiny-{bars}",
        candles=candles,
    )


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


def test_split_from_the_start_is_unchanged_when_no_anchor_is_asked_for() -> None:
    """The historical cut, to the bar: an existing caller must not move because of the anchor.

    `split` and `split_from_start` are two different explicit requests and they must agree
    bar for bar; the default is the historical one, so nothing that ran before the anchor
    existed changes its windows.
    """
    dataset = make_dataset(100)
    default = split_dataset(dataset, token="operator-2026")
    explicit = split_dataset(dataset, token="operator-2026", anchor=False)
    assert default.train == explicit.train == dataset.candles[:60]
    assert default.validation == explicit.validation == dataset.candles[60:80]
    assert default.holdout.size == explicit.holdout.size == 20


def test_an_anchored_split_cuts_the_tail_by_the_same_fractions() -> None:
    dataset = make_dataset(100)
    split = split_dataset(dataset, token="t", anchor=True)
    assert len(split.train) == 60
    assert len(split.validation) == 20
    assert split.holdout.size == 20
    assert split.train[0] == dataset.candles[0]
    assert split.validation[0] == dataset.candles[60]
    assert split.holdout.start == dataset.candles[80].open_time
    assert split.anchored is True
    # On a series that carries no older history to drop, anchoring changes no bound: the same
    # bar indices come out, which is what makes the anchored cut a refinement, not a rewrite.
    sliding = split_dataset(dataset, token="t")
    assert len(sliding.train) == len(split.train)
    assert sliding.validation == split.validation
    assert sliding.holdout.size == split.holdout.size
    assert sliding.holdout.start == split.holdout.start


def test_anchoring_keeps_the_newest_windows_fixed_when_history_is_prepended() -> None:
    """The point of the anchor: the *newest* bars must not slide backwards in time.

    Prepending 4 000 older bars takes the series from 1 000 to 5 000. The historical cut then
    reads bars 3 000..5 000, i.e. a different market period from the 1 000-bar run; the
    anchored cut keeps its tail glued to the *end* of the tape, so the newest bars of the long
    run are exactly the newest bars of the short one, and the training window is what grew.
    """
    dataset = make_dataset(1_000)
    longer = prepend_bars(dataset, 4_000)
    assert longer.bars == 5_000
    short = split_dataset(dataset, token="t", anchor=True)
    long = split_dataset(longer, token="t", anchor=True)
    # The tail is a fixed share of the tape (40 % of 5 000 = 2 000 bars, 3 000 of them train),
    # so the long run's tail strictly contains the short run's.
    assert len(long.train) == 3_000
    assert len(long.validation) + long.holdout.size == 2_000
    assert len(long.train) > len(short.train)
    assert long.bars == longer.bars
    # The newest windows did not move: the short run's whole tape -- validation and sealed
    # window included -- is inside the period the long run's tail covers, and that tail ends on
    # the very same candle.
    assert long.holdout.end == short.holdout.end == longer.candles[-1].close_time
    tail_start = long.validation[0].open_time
    assert tail_start == long.train[-1].close_time
    assert tail_start <= dataset.candles[0].open_time
    assert dataset.candles[-1].close_time <= long.holdout.end
    # The windows still tile the series with no gap and no overlap.
    assert long.holdout.start == long.validation[-1].close_time


def test_an_unanchored_split_really_does_slide_the_windows() -> None:
    """The defect the anchor removes, pinned so it cannot come back unnoticed.

    Prepending history takes the historical cut's validation window off the newest bars
    entirely: the long run validates on a period that *ends* before the short run's own sealed
    window even begins. No threshold can repair that, which is why the comparison was not
    controlled.
    """
    dataset = make_dataset(1_000)
    longer = prepend_bars(dataset, 4_000)
    short = split_dataset(dataset, token="t")
    long = split_dataset(longer, token="t")
    assert long.validation != short.validation
    assert long.holdout.start < short.holdout.start
    assert long.holdout.end == short.holdout.end
    # The long run stops validating before the short run's sealed window opens: the two runs
    # share neither their validation period nor their holdout period.
    assert long.validation[-1].close_time < short.holdout.start
    assert long.validation[0].open_time < short.validation[0].open_time


def test_split_refuses_fractions_that_leave_no_holdout() -> None:
    dataset = make_dataset(100)
    with pytest.raises(ValueError, match="holdout"):
        split_dataset(dataset, token="t", train_fraction=0.8, validation_fraction=0.2)


def test_split_refuses_a_tail_too_short_to_anchor() -> None:
    """Two candles cannot be cut into a train, a validation and a sealed holdout."""
    for bars in (1, 2, 3):
        with pytest.raises(ValueError, match="cannot form train"):
            split_dataset(make_tiny_dataset(bars), token="t", anchor=True)
        with pytest.raises(ValueError, match="cannot form train"):
            split_dataset(make_tiny_dataset(bars), token="t")


def test_a_split_reports_the_window_each_partition_actually_covers() -> None:
    """A comparison of two runs must be able to say *which* periods it compared."""
    split, dataset = split_of()
    windows = split.windows
    names = [window.name for window in windows]
    assert names == ["train", "validation", "holdout"]
    bars = [window.bars for window in windows]
    assert bars == [60, 20, 20]
    assert sum(bars) == split.bars
    train, validation, holdout = windows
    assert isinstance(train, DataWindow)
    assert train.start == dataset.candles[0].open_time
    assert train.end == dataset.candles[59].close_time
    assert validation.start == dataset.candles[60].open_time
    assert validation.end == dataset.candles[79].close_time
    assert holdout.start == dataset.candles[80].open_time
    assert holdout.end == dataset.candles[-1].close_time
    assert holdout.anchored is False
    # The partitions tile the series: no bar is counted twice and none is dropped.
    assert train.end == validation.start
    assert validation.end == holdout.start


def test_reading_a_window_never_unseals_the_holdout() -> None:
    """The report may name the sealed period; it may not read it."""
    split, _ = split_of()
    before = split.holdout.unlock_count
    _ = split.windows
    assert split.holdout.unlock_count == before
    with pytest.raises(SealedAccessError):
        _ = split.holdout.candles


def test_an_anchored_split_reports_itself_as_anchored() -> None:
    sliding, _ = split_of()
    anchored = split_dataset(make_dataset(100), token="operator-2026", anchor=True)
    assert [window.name for window in anchored.windows] == [
        window.name for window in sliding.windows
    ]
    assert [window.bars for window in anchored.windows] == [
        window.bars for window in sliding.windows
    ]
    assert all(window.anchored for window in anchored.windows)
    assert all(not window.anchored for window in sliding.windows)


def test_a_window_serialises_to_json_ready_values() -> None:
    """A report has to carry the window as data, not as a datetime a JSON writer chokes on."""
    split, _ = split_of()
    document = split.windows[1].to_dict()
    assert document == {
        "name": "validation",
        "start": split.validation[0].open_time.isoformat(),
        "end": split.validation[-1].close_time.isoformat(),
        "bars": 20,
        "anchored": False,
    }


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


def test_an_uncapped_walk_forward_advances_to_the_end_of_the_history() -> None:
    """The defect: a fold cap turned "roll to the end" into "look at the oldest slice".

    With ``max_folds=None`` the rolling origin must walk until the window no longer fits, so
    the *last* fold's validation block is the newest one the series can offer. A ceiling that
    leaves folds 0..k of a much longer series is a verdict on the oldest history only, and
    comparing two series of different lengths would compare two disjoint windows.
    """
    dataset = make_dataset(200)
    plan = WalkForwardPlan(train_bars=30, validation_bars=10, step_bars=10)
    folds = walk_forward(dataset.candles, plan)
    assert len(folds) == 17
    assert folds[-1].validation[-1] == dataset.candles[-1]
    assert folds[-1].validation == dataset.candles[-10:]
    assert folds[-1].train == dataset.candles[-40:-10]
    # Every fold still retrains on the bars immediately before its own validation block.
    for fold in folds:
        assert fold.validation[0].open_time == fold.train[-1].close_time


def test_more_history_gives_more_folds_when_no_cap_is_set() -> None:
    plan = WalkForwardPlan(train_bars=30, validation_bars=10, step_bars=10)
    short = walk_forward(make_dataset(200).candles, plan)
    long = walk_forward(make_dataset(400).candles, plan)
    assert len(long) > len(short)
    # And the capped plan refuses to grow: that is exactly what the diagnosis measured.
    capped = WalkForwardPlan(train_bars=30, validation_bars=10, step_bars=10, max_folds=6)
    assert len(walk_forward(make_dataset(400).candles, capped)) == 6


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
