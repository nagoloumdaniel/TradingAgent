"""Multi-family discovery (section 7): breadth, honesty, and no look-ahead.

The laboratory must explore several genuinely different families, and it must say out loud
which candidates died and why. These tests also pin the structural claims: a template is a
pure function of its grid, the report is deterministic, an over-fitted family loses on the
rolling origin, and a strategy that would need a future candle can never see one.
"""

import importlib
import json
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Self

import pytest
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradingagent.analytics.model import Performance, Trade
from tradingagent.analytics.performance import compute_performance
from tradingagent.backtest.datasets import CandleDataset, SyntheticRegime, synthetic_dataset
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.signal import SignalCandidate
from tradingagent.core.timeframe import Timeframe
from tradingagent.indicators.volatility import atr
from tradingagent.research import discovery as discovery_module
from tradingagent.research.discovery import (
    FAMILIES,
    CandidateOutcome,
    CandidateProposal,
    DiscardCause,
    DiscoveryProtocol,
    FamilyTemplate,
    Grid,
    TemplateScope,
    WalkForwardOutcome,
    benjamini_hochberg,
    bonferroni_threshold,
    build_proposal,
    control_false_discoveries,
    default_grid,
    discard_cause,
    discover,
    early_discard_cause,
    grid_values,
    monte_carlo_p_value,
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


def loose_protocol(**overrides: object) -> DiscoveryProtocol:
    """Every gate opened, so candidates actually reach the sealed set and the correction.

    The false-discovery control only has something to bite on when candidates survive the
    protocol; a test that wants survivors uses this rather than pretending the ladder passed.
    """
    return small_protocol(
        min_walk_forward_ratio=0.0,
        max_parameter_dispersion=10_000.0,
        min_stability_score=0.0,
        min_profitable_regime_ratio=0.0,
        min_out_of_sample_retention=-10.0,
        min_trades=1,
        **overrides,
    )


def tiny_grid() -> dict[str, dict[str, tuple[float, ...]]]:
    return {
        "trend_following": {"ema_fast": (5.0,), "ema_slow": (12.0,), "take_profit_rr": (1.5,)},
        "momentum": {"momentum_period": (10.0,), "threshold_atr": (1.0,)},
        "mean_reversion": {"rsi_period": (14.0,), "oversold": (35.0,), "overbought": (65.0,)},
        "breakout": {"channel_period": (10.0,), "trend_period": (20.0,)},
        "volatility_breakout": {"breakout_atr": (0.5,)},
        "ensemble": {"ema_fast": (5.0,), "ema_slow": (12.0,), "rsi_bull": (52.0,)},
    }


def synthetic(
    seed: int = 11, drift: float = 0.0, volatility: float = 0.003, bars: int = BARS
) -> CandleDataset:
    return synthetic_dataset(
        f"synthetic-{seed}-{drift}-{bars}",
        "frxXAUUSD",
        M15,
        START,
        (SyntheticRegime(bars=bars, drift=drift, volatility=volatility),),
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
        ScalpTripleFilter,
        VolatilityBreakout,
    )

    return {
        "mean_reversion": MeanReversion,
        "momentum": Momentum,
        "volatility_breakout": VolatilityBreakout,
        "consensus_ensemble": ConsensusEnsemble,
        "scalp_triple_filter": ScalpTripleFilter,
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
        "retained_before_correction": 0,
        "retained": 0,
        "discarded": 0,
        "failures": [],
    }
    assert report.multiple_testing.hypotheses == 0
    assert report.multiple_testing.bonferroni_threshold == 1.0
    assert report.multiple_testing.expected_false_discoveries == 0.0


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
    # A fold must be wide enough to hold the memoriser's declared history *and* trade: a
    # rolling origin that cannot run would say `insufficient_data`, which is a fact about the
    # window, not the overfitting this test is about.
    drifting = synthetic(seed=7, drift=0.004, volatility=0.0008, bars=1200)
    # The band is read off the training window, but after the warm-up of the manifest:
    # the whole point is a parameter fitted on the in-sample tape.
    window = drifting.candles[55:75]
    band_low = min(candle.low for candle in window)
    band_high = max(candle.high for candle in window)
    grid = {"overfitted": {"band_low": (band_low,), "band_high": (band_high,)}}
    families = (FamilyTemplate("overfitted", "gabarit surajusté", overfitted_template),)
    protocol = small_protocol(
        min_trades=1,
        walk_forward=WalkForwardPlan(
            train_bars=200, validation_bars=200, step_bars=200, max_folds=3
        ),
    )

    report = discover({"frxXAUUSD": drifting}, grid, protocol, families=families)

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
        assert candidate.walk_forward.folds >= 1
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

    loose = loose_protocol()
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


# --------------------------------------------------------------------------------------
# Multiple testing: a survivor of fifty-one attempts is not yet a discovery.
# --------------------------------------------------------------------------------------


def candidate_outcome(
    *,
    label: str = "probe:00",
    family: str = "probe",
    retained: bool = True,
    cause: DiscardCause | None = None,
    p_value: float | None = None,
) -> CandidateOutcome:
    """One hand-built verdict, so the correction is tested on written-down p-values."""
    return CandidateOutcome(
        market="frxXAUUSD",
        family=family,
        label=label,
        strategy_id="probe",
        version="0.1.0",
        parameters={},
        retained=retained,
        cause=cause,
        p_value=p_value,
    )


def test_the_documented_defaults_of_the_correction() -> None:
    protocol = DiscoveryProtocol()
    assert protocol.false_discovery_rate == 0.10
    assert protocol.monte_carlo_iterations == 1_000


def test_a_single_coin_flip_is_the_whole_null_for_a_single_trade() -> None:
    # One trade of +1: the null draws +1 or -1, so exactly half of the draws reach the
    # observed total. 0.5 is what the method must return, up to Monte-Carlo noise.
    p_value = monte_carlo_p_value([1.0], iterations=4_000, seed=11)
    assert 0.45 < p_value <= 0.55
    assert p_value == monte_carlo_p_value([1.0], iterations=4_000, seed=11)


def test_a_tape_that_only_lost_gets_a_p_value_of_one() -> None:
    # Flipping a sign can only improve a losing tape, so every draw is at least as good as
    # the observed total, and the p-value saturates at 1.0.
    assert monte_carlo_p_value([-1.0, -1.0, -1.0], iterations=500, seed=3) == 1.0
    assert monte_carlo_p_value([0.0, 0.0], iterations=500, seed=3) == 1.0
    assert monte_carlo_p_value([], iterations=500, seed=3) == 1.0


def test_ten_winning_trades_are_unlikely_under_the_no_edge_null() -> None:
    # Reaching +10 requires ten positive signs out of ten: 1 / 2**10 = 1/1024.
    p_value = monte_carlo_p_value([1.0] * 10, iterations=5_000, seed=5)
    assert 0.0 < p_value <= 0.01
    assert p_value == monte_carlo_p_value([1.0] * 10, iterations=5_000, seed=5)


def test_the_monte_carlo_p_value_refuses_a_degenerate_resolution() -> None:
    with pytest.raises(ValueError):
        monte_carlo_p_value([1.0], iterations=0)


def test_benjamini_hochberg_leaves_a_selection_it_has_no_reason_to_change() -> None:
    # m = 3, alpha = 0.10: the ranked thresholds are 0.0333, 0.0667 and 0.1000, and every
    # p-value clears its own. Nothing is corrected because nothing needed correcting.
    assert benjamini_hochberg([0.01, 0.02, 0.03], alpha=0.10) == (True, True, True)


def test_benjamini_hochberg_eliminates_the_candidate_that_chance_could_produce() -> None:
    # m = 2, alpha = 0.10: thresholds 0.05 and 0.10. 0.04 clears the first rank, 0.20
    # clears nothing -- the second candidate is the price of having tried two.
    assert benjamini_hochberg([0.04, 0.20], alpha=0.10) == (True, False)
    # The step-up stops at the highest qualifying rank: rank 2 qualifies (0.04 <= 0.0667),
    # rank 3 does not (0.15 > 0.1000), so the cut-off is 0.04.
    assert benjamini_hochberg([0.03, 0.04, 0.15], alpha=0.10) == (True, True, False)


def test_one_candidate_is_only_ever_compared_to_alpha() -> None:
    assert benjamini_hochberg([0.04], alpha=0.10) == (True,)
    assert benjamini_hochberg([0.20], alpha=0.10) == (False,)
    assert benjamini_hochberg([], alpha=0.10) == ()


def test_no_qualifying_rank_keeps_nothing() -> None:
    assert benjamini_hochberg([0.5, 0.6], alpha=0.10) == (False, False)


def test_the_bonferroni_threshold_is_reported_for_comparison_only() -> None:
    assert bonferroni_threshold(5, alpha=0.10) == 0.02
    assert bonferroni_threshold(51, alpha=0.10) == 0.10 / 51
    assert bonferroni_threshold(0, alpha=0.10) == 1.0


def test_the_correction_leaves_a_clean_selection_untouched() -> None:
    candidates = (
        candidate_outcome(label="a", p_value=0.01),
        candidate_outcome(label="b", p_value=0.02),
        candidate_outcome(label="c", p_value=0.03),
    )
    controlled, multiple = control_false_discoveries(candidates, alpha=0.10)
    assert [item.retained for item in controlled] == [True, True, True]
    assert [item.cause for item in controlled] == [None, None, None]
    assert multiple.hypotheses == 3
    assert multiple.discoveries_before == 3
    assert multiple.discoveries_after == 3
    assert multiple.rejected_by_correction == 0
    assert multiple.bonferroni_threshold == 0.10 / 3
    assert multiple.expected_false_discoveries == pytest.approx(0.30)
    assert controlled == candidates


def test_the_correction_discards_the_survivor_chance_could_have_produced() -> None:
    candidates = (
        candidate_outcome(label="signal", p_value=0.04),
        candidate_outcome(label="luck", p_value=0.20),
    )
    controlled, multiple = control_false_discoveries(candidates, alpha=0.10)
    assert [item.retained for item in controlled] == [True, False]
    assert controlled[1].cause is DiscardCause.FALSE_DISCOVERY
    assert controlled[1].p_value == 0.20  # the evidence is kept, not erased
    # The rank-2 threshold is 2 / 2 * 0.10 = 0.1000, and 0.20 does not clear it.
    assert controlled[1].detail == (
        "p=0.2000 > seuil de Benjamini-Hochberg 0.1000 au rang 2 sur 2 test(s) à alpha=0.10"
    )
    assert multiple.discoveries_before == 2
    assert multiple.discoveries_after == 1
    assert multiple.rejected_by_correction == 1
    assert multiple.bonferroni_threshold == 0.05
    assert multiple.expected_false_discoveries == 0.20
    assert multiple.expected_false_discovery_rate == 0.10


def test_a_single_survivor_is_its_own_selection() -> None:
    kept, multiple = control_false_discoveries((candidate_outcome(p_value=0.04),), alpha=0.10)
    assert kept[0].retained is True
    assert multiple.hypotheses == 1
    assert multiple.discoveries_after == 1
    dropped, single = control_false_discoveries((candidate_outcome(p_value=0.20),), alpha=0.10)
    assert dropped[0].retained is False
    assert dropped[0].cause is DiscardCause.FALSE_DISCOVERY
    assert single.hypotheses == 1
    assert single.discoveries_before == 1
    assert single.discoveries_after == 0


def test_a_candidate_that_never_reached_the_holdout_counts_as_a_test_at_p_one() -> None:
    candidates = (
        candidate_outcome(label="signal", p_value=0.04),
        candidate_outcome(label="dead", retained=False, cause=DiscardCause.OVERFITTING),
    )
    controlled, multiple = control_false_discoveries(candidates, alpha=0.10)
    # Two candidates were tried, so two tests are paid for, even though only one has a
    # p-value; m = 2 is what makes this conservative rather than flattering.
    assert multiple.hypotheses == 2
    assert multiple.bonferroni_threshold == 0.05
    assert [item.retained for item in controlled] == [True, False]
    assert [item.cause for item in controlled] == [None, DiscardCause.OVERFITTING]


def test_a_survivor_without_any_p_value_is_never_kept_silently() -> None:
    controlled, multiple = control_false_discoveries((candidate_outcome(p_value=None),), alpha=0.10)
    assert controlled[0].retained is False
    assert controlled[0].cause is DiscardCause.FALSE_DISCOVERY
    assert multiple.discoveries_before == 1
    assert multiple.discoveries_after == 0


def test_the_correction_is_deterministic() -> None:
    candidates = (
        candidate_outcome(label="a", p_value=0.03),
        candidate_outcome(label="b", p_value=0.04),
        candidate_outcome(label="c", p_value=0.15),
    )
    first, first_report = control_false_discoveries(candidates, alpha=0.10)
    second, second_report = control_false_discoveries(candidates, alpha=0.10)
    assert first == second
    assert first_report == second_report


def test_the_run_prices_every_survivor_and_corrects_the_whole_selection() -> None:
    report = discover({"frxXAUUSD": synthetic()}, tiny_grid(), loose_protocol())
    multiple = report.multiple_testing
    assert multiple.method == "benjamini_hochberg"
    assert multiple.alpha == 0.10
    assert multiple.hypotheses == len(report.candidates)
    assert multiple.bonferroni_threshold == 0.10 / len(report.candidates)
    assert multiple.expected_false_discoveries == len(report.candidates) * 0.10
    assert multiple.discoveries_before == (
        multiple.discoveries_after + multiple.rejected_by_correction
    )
    assert multiple.discoveries_after == len(report.retained())
    for candidate in report.candidates:
        if candidate.retained or candidate.cause is DiscardCause.FALSE_DISCOVERY:
            assert candidate.p_value is not None
            assert 0.0 < candidate.p_value <= 1.0
        else:
            # A candidate that never earned its holdout read has no p-value to offer, and
            # must not be handed a flattering one.
            assert candidate.p_value is None
    for summary in report.families:
        assert summary.retained_before_correction >= summary.retained
        assert summary.retained_before_correction == (
            summary.retained + summary.failures.get(DiscardCause.FALSE_DISCOVERY, 0)
        )
        assert summary.expected_false_discoveries == summary.tested * 0.10


def test_the_report_json_carries_the_selection_figures_in_a_stable_shape() -> None:
    report = discover({"frxXAUUSD": synthetic()}, tiny_grid(), loose_protocol())
    payload = report.to_dict()
    assert set(payload["multiple_testing"]) == {
        "method",
        "alpha",
        "expected_false_discovery_rate",
        "expected_false_discoveries",
        "hypotheses",
        "bonferroni_threshold",
        "discoveries_before",
        "discoveries_after",
        "rejected_by_correction",
    }
    assert payload["multiple_testing"]["method"] == "benjamini_hochberg"
    assert payload["multiple_testing"]["hypotheses"] == len(report.candidates)
    assert payload["multiple_testing"]["bonferroni_threshold"] == 0.10 / len(report.candidates)
    assert payload["multiple_testing"]["expected_false_discovery_rate"] == 0.10
    assert payload["totals"]["retained_before_correction"] == (
        report.multiple_testing.discoveries_before
    )
    assert payload["protocol"]["false_discovery_rate"] == 0.10
    assert payload["protocol"]["monte_carlo_iterations"] == 1_000
    for family in payload["families"]:
        assert set(family) >= {"retained_before_correction", "expected_false_discoveries"}
    assert all("p_value" in candidate for candidate in payload["candidates"])


def test_a_run_whose_survivors_do_not_clear_the_correction_discards_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A written-down p-value for every survivor: 0.20 with m candidates and alpha = 0.10
    # clears no rank (rank 1 would need p <= 0.10 / m), so nothing survives.
    monkeypatch.setattr(discovery_module, "monte_carlo_p_value", lambda *_, **__: 0.20)
    report = discover({"frxXAUUSD": synthetic()}, tiny_grid(), loose_protocol())
    before = report.multiple_testing.discoveries_before
    assert before >= 1
    assert report.multiple_testing.discoveries_after == 0
    assert report.multiple_testing.rejected_by_correction == before
    assert report.retained() == ()
    demoted = [
        candidate
        for candidate in report.candidates
        if candidate.cause is DiscardCause.FALSE_DISCOVERY
    ]
    assert len(demoted) == before
    assert all(candidate.p_value == 0.20 for candidate in demoted)
    assert all(candidate.retained is False for candidate in demoted)
    assert report.failures_by_cause()[DiscardCause.FALSE_DISCOVERY] == before
    assert report.to_dict()["totals"]["retained"] == 0
    assert report.to_dict()["totals"]["retained_before_correction"] == before


def test_the_cli_summary_shows_the_figures_before_and_after_the_correction(
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = importlib.import_module("scripts.backtest.discover")
    report = discover({"frxXAUUSD": synthetic()}, tiny_grid(), loose_protocol())
    script.print_report(report)
    printed = capsys.readouterr().out
    assert "Benjamini-Hochberg" in printed
    assert "seuil de Bonferroni" in printed
    assert "retenus avant correction" in printed
    assert "fausses découvertes attendues" in printed
    for summary in report.families:
        assert summary.family in printed
