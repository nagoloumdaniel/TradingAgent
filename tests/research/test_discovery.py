"""Multi-family discovery (section 7): breadth, honesty, and no look-ahead.

The laboratory must explore several genuinely different families, and it must say out loud
which candidates died and why. These tests also pin the structural claims: a template is a
pure function of its grid, the report is deterministic, an over-fitted family loses on the
rolling origin, and a strategy that would need a future candle can never see one.
"""

import json
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradingagent.analytics.model import Performance, Trade
from tradingagent.analytics.performance import compute_performance
from tradingagent.backtest.datasets import CandleDataset, SyntheticRegime, synthetic_dataset
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.signal import SignalCandidate
from tradingagent.core.timeframe import Timeframe
from tradingagent.indicators.volatility import atr
from tradingagent.research.discovery import (
    FAMILIES,
    CandidateProposal,
    DiscardCause,
    DiscoveryProtocol,
    FamilyTemplate,
    Grid,
    TemplateScope,
    WalkForwardOutcome,
    build_proposal,
    default_grid,
    discard_cause,
    discover,
    early_discard_cause,
    grid_values,
)
from tradingagent.research.protocol import (
    StabilityReport,
    WalkForwardPlan,
)
from tradingagent.strategies.base import Strategy, StrategyContext
from tradingagent.strategies.registry import REGISTRY

START = datetime(2026, 1, 5, tzinfo=UTC)
M15 = Timeframe.M15
BARS = 500


def small_protocol(**overrides: object) -> DiscoveryProtocol:
    """A protocol with a short rolling origin, so a test dataset can support several folds."""
    base = DiscoveryProtocol(
        min_trades=3,
        walk_forward=WalkForwardPlan(train_bars=80, validation_bars=60, step_bars=60, max_folds=3),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def tiny_grid() -> dict[str, dict[str, tuple[float, ...]]]:
    return {
        "trend_following": {"ema_fast": (5.0,), "ema_slow": (12.0,), "take_profit_rr": (1.5,)},
        "momentum": {"momentum_period": (10.0,), "threshold_atr": (1.0,)},
        "mean_reversion": {"rsi_period": (14.0,), "oversold": (35.0,), "overbought": (65.0,)},
        "breakout": {"channel_period": (10.0,), "trend_period": (20.0,)},
        "volatility_breakout": {"breakout_atr": (0.5,)},
        "ensemble": {"ema_fast": (5.0,), "ema_slow": (12.0,), "rsi_bull": (52.0,)},
    }


def synthetic(seed: int = 11, drift: float = 0.0, volatility: float = 0.003) -> CandleDataset:
    return synthetic_dataset(
        f"synthetic-{seed}-{drift}",
        "frxXAUUSD",
        M15,
        START,
        (SyntheticRegime(bars=BARS, drift=drift, volatility=volatility),),
        seed=seed,
        decimals=5,
    )


def performance_of(pnls: Sequence[float]) -> Performance:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    trades = [
        Trade(
            symbol="frxXAUUSD",
            strategy_ref="probe@0.1.0",
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


def stability(
    *,
    score: float = 0.9,
    dispersion: float = 0.0,
    regimes: float = 1.0,
    trades: int = 40,
) -> StabilityReport:
    return StabilityReport(
        score=score,
        out_of_sample_retention=1.0,
        parameter_dispersion=dispersion,
        profitable_regime_ratio=regimes,
        trades=trades,
        fragile=score < 0.5,
        reasons=(),
    )


def walk_forward_outcome(*, folds: int = 4, profitable: int = 4) -> WalkForwardOutcome:
    return WalkForwardOutcome(
        folds=folds,
        profitable_folds=profitable,
        trades=40,
        net_profit=Decimal("10"),
    )


# --------------------------------------------------------------------------------------
# The catalogue: several genuinely different families.
# --------------------------------------------------------------------------------------


def test_the_catalogue_covers_at_least_four_really_different_families() -> None:
    names = [template.family for template in FAMILIES]
    assert len(names) == len(set(names))
    assert {"trend_following", "momentum", "mean_reversion", "breakout"} <= set(names)
    assert {"volatility_breakout", "ensemble"} <= set(names)
    for template in FAMILIES:
        assert template.description.strip()


def test_every_family_proposes_valid_parameters_for_a_real_strategy_class() -> None:
    scope = TemplateScope(symbols=("frxXAUUSD",), timeframe=M15)
    for template in FAMILIES:
        proposals = list(template.template(scope, tiny_grid().get(template.family, {})))
        assert proposals, f"{template.family} produced no candidate"
        for proposal in proposals:
            assert proposal.family == template.family
            strategy_class = (
                REGISTRY.get(proposal.strategy_id) or _research_classes()[proposal.strategy_id]
            )
            model = strategy_class.parameters_model
            model.model_validate(dict(proposal.parameters))
            assert proposal.manifest.strategy_id == proposal.strategy_id
            assert proposal.manifest.history_bars >= 30
            assert proposal.manifest.parameters == dict(proposal.parameters)


def _research_classes() -> dict[str, type[Strategy[Any]]]:
    from tradingagent.research.discovery import (
        ConsensusEnsemble,
        MeanReversion,
        Momentum,
        VolatilityBreakout,
    )

    return {
        "mean_reversion": MeanReversion,
        "momentum": Momentum,
        "volatility_breakout": VolatilityBreakout,
        "consensus_ensemble": ConsensusEnsemble,
    }


def test_discovered_families_are_candidates_not_executable_strategies() -> None:
    """A discovered family must not be in the production registry: discovering is not promoting."""
    executable = set(REGISTRY)
    assert {"witness", "trend_breakout"} <= executable
    assert not {"mean_reversion", "momentum", "volatility_breakout", "consensus_ensemble"} & set(
        executable
    )


def test_templates_are_pure_functions_of_the_grid() -> None:
    scope = TemplateScope(symbols=("frxXAUUSD",), timeframe=M15)
    for template in FAMILIES:
        grid = tiny_grid().get(template.family, {})
        first = [
            (item.strategy_id, dict(item.parameters), item.manifest.history_bars)
            for item in template.template(scope, grid)
        ]
        second = [
            (item.strategy_id, dict(item.parameters), item.manifest.history_bars)
            for item in template.template(scope, grid)
        ]
        assert first == second
        assert first


def test_integral_periods_stay_integral_so_perturbation_can_round_them() -> None:
    """A period stored as 14.0 would be perturbed to 15.4, which is not a strategy."""
    scope = TemplateScope(symbols=("frxXAUUSD",), timeframe=M15)
    for template in FAMILIES:
        for proposal in template.template(scope, tiny_grid().get(template.family, {})):
            for key in ("atr_period", "ema_fast", "ema_slow", "channel_period", "rsi_period"):
                if key in proposal.parameters:
                    assert isinstance(proposal.parameters[key], int)


# --------------------------------------------------------------------------------------
# The run: every family is explored, and the failure table is honest.
# --------------------------------------------------------------------------------------


def test_discovery_explores_every_family_and_reports_causes() -> None:
    report = discover({"frxXAUUSD": synthetic()}, tiny_grid(), small_protocol())
    assert len(report.markets) == 1
    assert report.markets[0].skipped is None
    assert report.markets[0].walk_forward_folds >= 1
    assert len(report.candidates) >= len(FAMILIES)
    for template in FAMILIES:
        summary = report.family(template.family)
        assert summary is not None
        tested = [item for item in report.candidates if item.family == template.family]
        assert summary.tested == len(tested)
        assert summary.retained + summary.discarded == summary.tested
        assert sum(summary.failures.values()) == summary.discarded
        for item in tested:
            assert (item.cause is None) is item.retained
            if item.retained:
                assert item.out_of_sample is not None
    assert sum(report.failures_by_cause().values()) == len(report.discarded())
    assert set(report.failures_by_cause()) <= set(DiscardCause)


def test_report_is_deterministic_and_carries_no_wall_clock() -> None:
    datasets = {"frxXAUUSD": synthetic()}
    first = discover(datasets, tiny_grid(), small_protocol())
    second = discover(datasets, tiny_grid(), small_protocol())
    assert first.to_dict() == second.to_dict()
    assert "generated_at" not in first.to_dict()
    assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(
        second.to_dict(), sort_keys=True
    )


def test_an_empty_dataset_mapping_produces_an_empty_report_instead_of_crashing() -> None:
    report = discover({}, {}, small_protocol())
    assert report.markets == ()
    assert report.candidates == ()
    assert report.retained() == ()
    assert len(report.families) == len(FAMILIES)
    assert all(summary.tested == 0 for summary in report.families)
    assert report.failures_by_cause() == {}
    assert report.to_dict()["totals"] == {
        "tested": 0,
        "retained": 0,
        "discarded": 0,
        "failures": [],
    }


def test_a_dataset_too_short_to_split_is_reported_as_skipped() -> None:
    short = synthetic_dataset(
        "short",
        "frxXAUUSD",
        M15,
        START,
        (SyntheticRegime(bars=20, drift=0.0, volatility=0.002),),
        seed=1,
    )
    report = discover({"frxXAUUSD": short}, tiny_grid(), small_protocol())
    assert report.candidates == ()
    assert report.markets[0].skipped is not None
    assert report.to_dict()["markets"][0]["skipped"]


def test_an_empty_grid_falls_back_to_the_documented_defaults() -> None:
    report = discover({"frxXAUUSD": synthetic()}, {}, small_protocol())
    assert len(report.candidates) > len(FAMILIES)
    assert all(candidate.cause is not None for candidate in report.candidates)


def test_the_default_grid_covers_every_catalogue_family() -> None:
    grid = default_grid()
    assert set(grid) == {template.family for template in FAMILIES}
    for values in grid.values():
        assert values
        for key, options in values.items():
            assert key
            assert options


# --------------------------------------------------------------------------------------
# The discard ladder: one precise reason per candidate.
# --------------------------------------------------------------------------------------


def test_too_few_trades_outranks_every_other_gate() -> None:
    cause = early_discard_cause(
        stability(regimes=0.0),
        walk_forward_outcome(profitable=0),
        in_sample_trades=2,
        protocol=small_protocol(),
    )
    assert cause is DiscardCause.TOO_FEW_TRADES


def test_a_walk_forward_that_never_wins_is_overfitting() -> None:
    cause = early_discard_cause(
        stability(),
        walk_forward_outcome(folds=4, profitable=1),
        in_sample_trades=40,
        protocol=small_protocol(),
    )
    assert cause is DiscardCause.OVERFITTING


def test_a_parameter_island_is_reported_as_dispersion() -> None:
    cause = early_discard_cause(
        stability(dispersion=0.9),
        walk_forward_outcome(),
        in_sample_trades=40,
        protocol=small_protocol(),
    )
    assert cause is DiscardCause.PARAMETER_DISPERSION
    rejected = early_discard_cause(
        stability(dispersion=0.1),
        walk_forward_outcome(),
        in_sample_trades=40,
        protocol=small_protocol(),
        invalid_perturbations=2,
    )
    assert rejected is DiscardCause.PARAMETER_DISPERSION


def test_a_low_stability_score_is_unstable_and_a_negative_holdout_is_out_of_sample() -> None:
    weak = early_discard_cause(
        stability(score=0.2),
        walk_forward_outcome(),
        in_sample_trades=40,
        protocol=small_protocol(),
    )
    assert weak is DiscardCause.UNSTABLE
    negative = discard_cause(
        stability(),
        walk_forward_outcome(),
        performance_of([-3.0] * 5),
        0.0,
        in_sample_trades=40,
        protocol=small_protocol(),
    )
    assert negative is DiscardCause.OUT_OF_SAMPLE_NEGATIVE
    zero = discard_cause(
        stability(),
        walk_forward_outcome(),
        performance_of([]),
        0.0,
        in_sample_trades=40,
        protocol=small_protocol(),
    )
    assert zero is DiscardCause.OUT_OF_SAMPLE_NEGATIVE


def test_all_gates_passed_means_retained() -> None:
    cause = discard_cause(
        stability(),
        walk_forward_outcome(),
        performance_of([2.0] * 10),
        0.8,
        in_sample_trades=40,
        protocol=small_protocol(),
    )
    assert cause is None


# --------------------------------------------------------------------------------------
# The deliberately over-fitted family: it must lose on the rolling origin.
# --------------------------------------------------------------------------------------


class _BandParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    band_low: float = Field(gt=0)
    band_high: float = Field(gt=0)
    atr_period: int = Field(ge=1)
    stop_atr_multiplier: float = Field(gt=0)
    take_profit_rr: float = Field(gt=0)
    entry_zone_atr: float = Field(ge=0)

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if self.band_high <= self.band_low:
            raise ValueError("band_high must exceed band_low")
        if self.entry_zone_atr >= self.stop_atr_multiplier:
            raise ValueError("entry_zone_atr must be narrower than stop_atr_multiplier")
        return self


class _BandMemoriser(Strategy[_BandParameters]):
    """Only trades inside a price band lifted from the training window: textbook overfitting."""

    strategy_id = "band_memoriser"
    parameters_model = _BandParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        parameters = self.parameters
        timeframe = context.primary_timeframe
        closes = context.closes(timeframe)
        volatility = atr(
            context.highs(timeframe), context.lows(timeframe), closes, parameters.atr_period
        )[-1]
        if volatility is None or volatility <= 0:
            return None
        close = closes[-1]
        if not parameters.band_low <= close <= parameters.band_high:
            return None
        risk = parameters.stop_atr_multiplier * volatility
        zone = parameters.entry_zone_atr * volatility
        return SignalCandidate(
            direction=Direction.BUY,
            entry_low=close - zone,
            entry_high=close + zone,
            stop_loss=close - risk,
            take_profits=(close + parameters.take_profit_rr * risk,),
            reason="close inside the band memorised on the training window",
            indicators={"atr": volatility},
        )


def overfitted_template(scope: TemplateScope, grid: Grid) -> Iterator[CandidateProposal]:
    lows = grid_values(grid, "band_low", (1.0,))
    highs = grid_values(grid, "band_high", (2.0,))
    for band_low, band_high in zip(lows, highs, strict=True):
        if band_high <= band_low:
            continue
        yield build_proposal(
            scope,
            "overfitted",
            _BandMemoriser,
            {
                "band_low": band_low,
                "band_high": band_high,
                "atr_period": 14,
                # A wide entry zone and a distant stop, so the memorised band does trade
                # in-sample: an over-fitted family must be caught by the rolling origin,
                # not merely by a shortage of trades.
                "stop_atr_multiplier": 10.0,
                "take_profit_rr": 1.5,
                "entry_zone_atr": 5.0,
            },
            lookback=20,
        )


def test_a_family_fitted_on_the_training_window_is_discarded_as_overfitting() -> None:
    drifting = synthetic(seed=7, drift=0.004, volatility=0.0008)
    # The band is read off the training window, but after the warm-up of the manifest:
    # the whole point is a parameter fitted on the in-sample tape.
    window = drifting.candles[55:75]
    band_low = min(candle.low for candle in window)
    band_high = max(candle.high for candle in window)
    grid = {"overfitted": {"band_low": (band_low,), "band_high": (band_high,)}}
    families = (FamilyTemplate("overfitted", "gabarit surajusté", overfitted_template),)

    report = discover(
        {"frxXAUUSD": drifting}, grid, small_protocol(min_trades=1), families=families
    )

    assert report.candidates
    assert report.retained() == ()
    summary = report.family("overfitted")
    assert summary is not None
    assert summary.tested == len(report.candidates)
    assert summary.discarded == summary.tested
    assert summary.failures == {DiscardCause.OVERFITTING: summary.tested}
    for candidate in report.candidates:
        assert candidate.cause is DiscardCause.OVERFITTING
        assert candidate.walk_forward is not None
        assert candidate.walk_forward.ratio < 0.5
        assert candidate.train is not None
        assert candidate.train.trades >= 1
        assert candidate.out_of_sample is None
    assert report.markets[0].holdout_unlocks == 0


# --------------------------------------------------------------------------------------
# No look-ahead: the window given to a strategy never contains a future candle.
# --------------------------------------------------------------------------------------


LOOK_AHEAD_OBSERVATIONS: list[tuple[datetime, datetime]] = []


class _ProbeParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    marker: int = Field(ge=1)


class _LookAheadProbe(Strategy[_ProbeParameters]):
    """Records the newest close time it ever sees; raises if that is after the decision."""

    strategy_id = "look_ahead_probe"
    parameters_model = _ProbeParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        candles = context.candles[context.primary_timeframe]
        newest = max(candle.close_time for candle in candles)
        LOOK_AHEAD_OBSERVATIONS.append((context.evaluated_at, newest))
        if newest > context.evaluated_at:
            raise AssertionError("a candle closed after the decision was visible")
        # A rule that would need the *next* candle can never be written: there is none.
        return None


def cheating_template(scope: TemplateScope, grid: Grid) -> Iterator[CandidateProposal]:
    yield build_proposal(
        scope,
        "look_ahead",
        _LookAheadProbe,
        {"marker": 1},
        lookback=20,
    )


def test_a_strategy_that_would_need_a_future_candle_can_never_be_retained() -> None:
    LOOK_AHEAD_OBSERVATIONS.clear()
    families = (FamilyTemplate("look_ahead", "sonde d'anticipation", cheating_template),)
    report = discover({"frxXAUUSD": synthetic()}, {}, small_protocol(), families=families)

    assert LOOK_AHEAD_OBSERVATIONS
    assert all(newest <= evaluated_at for evaluated_at, newest in LOOK_AHEAD_OBSERVATIONS)
    assert report.retained() == ()
    assert len(report.candidates) == 1
    candidate = report.candidates[0]
    assert candidate.cause is DiscardCause.TOO_FEW_TRADES
    assert candidate.strategy_errors == ()
    assert candidate.out_of_sample is None
    assert report.markets[0].holdout_unlocks == 0


def test_the_holdout_is_only_read_after_the_rolling_gates() -> None:
    """A candidate that fails early leaves the sealed set untouched; a survivor opens it."""
    probe = (FamilyTemplate("look_ahead", "sonde d'anticipation", cheating_template),)
    failing = discover({"frxXAUUSD": synthetic()}, {}, small_protocol(), families=probe)
    assert failing.markets[0].holdout_unlocks == 0
    assert failing.candidates[0].holdout_was_read is False

    loose = small_protocol(
        min_walk_forward_ratio=0.0,
        max_parameter_dispersion=10_000.0,
        min_stability_score=0.0,
        min_profitable_regime_ratio=0.0,
        min_out_of_sample_retention=-10.0,
        min_trades=1,
    )
    trading = tuple(template for template in FAMILIES if template.family == "trend_following")
    passing = discover({"frxXAUUSD": synthetic()}, tiny_grid(), loose, families=trading)
    assert passing.candidates
    assert passing.markets[0].holdout_unlocks == len(passing.candidates)
    assert all(candidate.holdout_was_read for candidate in passing.candidates)


def test_a_broken_proposal_is_reported_as_invalid_parameters() -> None:
    def broken_template(scope: TemplateScope, grid: Grid) -> Iterator[CandidateProposal]:
        yield build_proposal(
            scope,
            "broken",
            _BandMemoriser,
            {
                "band_low": 10.0,
                "band_high": 5.0,
                "atr_period": 14,
                "stop_atr_multiplier": 1.5,
                "take_profit_rr": 1.5,
                "entry_zone_atr": 0.5,
            },
            lookback=20,
        )

    families = (FamilyTemplate("broken", "gabarit invalide", broken_template),)
    report = discover({"frxXAUUSD": synthetic()}, {}, small_protocol(), families=families)
    assert len(report.candidates) == 1
    assert report.candidates[0].cause is DiscardCause.INVALID_PARAMETERS
    assert report.candidates[0].detail
    assert report.family("broken") is not None
    totals = report.to_dict()["totals"]
    assert totals["tested"] == 1
    assert totals["retained"] == 0
    assert totals["discarded"] == 1
    assert totals["failures"] == [
        {
            "cause": DiscardCause.INVALID_PARAMETERS.value,
            "count": 1,
            "description": DiscardCause.INVALID_PARAMETERS.description,
        }
    ]
