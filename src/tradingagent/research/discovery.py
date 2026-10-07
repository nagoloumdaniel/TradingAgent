"""Multi-family strategy discovery for the research laboratory (F-025, section 7).

The point of this module is *breadth before depth*: the laboratory must be able to explore
several genuinely different families â€” trend following, momentum, mean reversion, channel
breakout, volatility breakout and an indicator consensus â€” instead of tuning the one family
somebody already believed in. No family is assumed to be better than another: every candidate
crosses the same harness, the same costs and the same anti-overfitting protocol, and the
report says out loud how many candidates each family lost, and why.

Structural guarantees
---------------------
* Decisions go through ``strategies.evaluation.evaluate`` (via
  ``backtest.harness.run_backtest``), the single production decision path: this module
  reimplements neither the time split nor a single indicator.
* A candidate is never retained without clearing, in order: enough in-sample trades,
  walk-forward on a rolling origin, parameter perturbation, the stability score, and finally
  the sealed out-of-sample set.
* The out-of-sample set is unlocked only for candidates that already cleared the rolling
  gates, so a family that fails early leaves the holdout sealed.
* A template is a pure function of a parameter grid. It cannot read a dataset, so it cannot
  fit a parameter to the very data it will be judged on.
* Trying fifty-one candidates and keeping the best one is a *selection*, not a discovery: the
  survivors of the ladder are therefore passed through a Benjamini-Hochberg false-discovery
  control. A survivor that does not clear it is discarded with an explicit cause, never kept
  because it looked good.

Discovering is not promoting: this module writes no manifest to ``config/strategies/`` and
never touches ``strategies/registry.py``. A discovered candidate becomes executable only once
the Lead promotes it.
"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum
from itertools import product
from typing import Any, Self
from zlib import crc32

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradingagent.analytics.model import Performance
from tradingagent.backtest.costs import CostModel
from tradingagent.backtest.datasets import CandleDataset
from tradingagent.backtest.harness import BacktestConfig, run_backtest
from tradingagent.backtest.randomness import DeterministicRandom
from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.signal import SignalCandidate
from tradingagent.core.timeframe import Timeframe
from tradingagent.indicators.momentum import rsi
from tradingagent.indicators.moving_average import ema
from tradingagent.indicators.volatility import atr
from tradingagent.research.protocol import (
    MIN_TRADES_FOR_STABILITY,
    OOS_RETENTION_MIN,
    PARAMETER_CV_MAX,
    PROFITABLE_REGIME_MIN,
    STABILITY_SCORE_MIN,
    Fold,
    SealedSet,
    StabilityReport,
    WalkForwardPlan,
    confirm,
    out_of_sample_retention,
    period_report,
    perturb_parameters,
    split_dataset,
    stability_report,
    walk_forward,
)
from tradingagent.strategies.base import Strategy, StrategyContext
from tradingagent.strategies.library.trend_breakout import TrendBreakout
from tradingagent.strategies.library.witness import Witness
from tradingagent.strategies.manifest import StrategyManifest

DEFAULT_WALK_FORWARD_RATIO = 0.5
DEFAULT_VERSION = "0.1.0"
#: Tolerated proportion of false discoveries among the candidates a run retains. 0.10 is the
#: usual convention and it is *not* sacred: the report always prints it next to the numbers.
DEFAULT_FALSE_DISCOVERY_RATE = 0.10
#: Draws of the sign-flip null used to estimate one candidate's out-of-sample p-value.
DEFAULT_MONTE_CARLO_ITERATIONS = 1_000


class DiscardCause(StrEnum):
    """Why a candidate was not retained. The order is the order of the gates."""

    INVALID_PARAMETERS = "invalid_parameters"
    INSUFFICIENT_DATA = "insufficient_data"
    TOO_FEW_TRADES = "too_few_trades"
    OVERFITTING = "overfitting"
    PARAMETER_DISPERSION = "parameter_dispersion"
    UNSTABLE = "unstable"
    OUT_OF_SAMPLE_NEGATIVE = "out_of_sample_negative"
    FALSE_DISCOVERY = "false_discovery"

    @property
    def description(self) -> str:
        return _CAUSE_DESCRIPTIONS[self]


_CAUSE_DESCRIPTIONS: dict[DiscardCause, str] = {
    DiscardCause.INVALID_PARAMETERS: "gabarit/paramètres refusés par le modèle pydantic",
    DiscardCause.INSUFFICIENT_DATA: "historique insuffisant pour découper ou évaluer",
    DiscardCause.TOO_FEW_TRADES: "trop peu d'opérations en échantillon d'apprentissage",
    DiscardCause.OVERFITTING: "walk-forward non profitable : surapprentissage",
    DiscardCause.PARAMETER_DISPERSION: "dispersion des paramètres : îlot instable",
    DiscardCause.UNSTABLE: "score de stabilité insuffisant",
    DiscardCause.OUT_OF_SAMPLE_NEGATIVE: "hors-échantillon scellé négatif ou rétention nulle",
    DiscardCause.FALSE_DISCOVERY: (
        "survivant de la sélection multiple : p-value au-dessus de la correction "
        "de Benjamini-Hochberg"
    ),
}


# --------------------------------------------------------------------------------------
# Families implemented here: candidates, never production strategies.
# --------------------------------------------------------------------------------------


def _signal(
    *,
    direction: Direction,
    close: float,
    volatility: float,
    stop_atr_multiplier: float,
    take_profit_rr: float,
    entry_zone_atr: float,
    reason: str,
    indicators: Mapping[str, float],
) -> SignalCandidate:
    """Shared level plumbing: the same entry/stop/target geometry as the library strategies."""
    risk = stop_atr_multiplier * volatility
    zone = entry_zone_atr * volatility
    if direction is Direction.BUY:
        stop_loss, take_profit = close - risk, close + take_profit_rr * risk
    else:
        stop_loss, take_profit = close + risk, close - take_profit_rr * risk
    return SignalCandidate(
        direction=direction,
        entry_low=close - zone,
        entry_high=close + zone,
        stop_loss=stop_loss,
        take_profits=(take_profit,),
        reason=reason,
        indicators=indicators,
    )


class MeanReversionParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rsi_period: int = Field(ge=2)
    oversold: float = Field(gt=0, lt=100)
    overbought: float = Field(gt=0, lt=100)
    atr_period: int = Field(ge=1)
    stop_atr_multiplier: float = Field(gt=0)
    take_profit_rr: float = Field(gt=0)
    entry_zone_atr: float = Field(ge=0)

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if self.oversold >= self.overbought:
            raise ValueError("oversold must be below overbought")
        if self.entry_zone_atr >= self.stop_atr_multiplier:
            raise ValueError("entry_zone_atr must be narrower than stop_atr_multiplier")
        if self.take_profit_rr * self.stop_atr_multiplier <= self.entry_zone_atr:
            raise ValueError("the take-profit must lie beyond the entry zone")
        return self


class MeanReversion(Strategy[MeanReversionParameters]):
    """Buy an oversold RSI, sell an overbought one. A research candidate, not a strategy."""

    strategy_id = "mean_reversion"
    parameters_model = MeanReversionParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        parameters = self.parameters
        timeframe = context.primary_timeframe
        closes = context.closes(timeframe)
        highs = context.highs(timeframe)
        lows = context.lows(timeframe)
        if len(closes) <= parameters.rsi_period:
            return None
        reading = rsi(closes, parameters.rsi_period)[-1]
        volatility = atr(highs, lows, closes, parameters.atr_period)[-1]
        if reading is None or volatility is None or volatility <= 0:
            return None
        if reading <= parameters.oversold:
            direction, side = Direction.BUY, "oversold"
        elif reading >= parameters.overbought:
            direction, side = Direction.SELL, "overbought"
        else:
            return None
        return _signal(
            direction=direction,
            close=closes[-1],
            volatility=volatility,
            stop_atr_multiplier=parameters.stop_atr_multiplier,
            take_profit_rr=parameters.take_profit_rr,
            entry_zone_atr=parameters.entry_zone_atr,
            reason=(
                f"RSI{parameters.rsi_period} {reading:.1f} {side}; stop "
                f"{parameters.stop_atr_multiplier} x ATR{parameters.atr_period}, target "
                f"{parameters.take_profit_rr}R"
            ),
            indicators={"rsi": reading, "atr": volatility},
        )


class MomentumParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    momentum_period: int = Field(ge=1)
    threshold_atr: float = Field(gt=0)
    atr_period: int = Field(ge=1)
    stop_atr_multiplier: float = Field(gt=0)
    take_profit_rr: float = Field(gt=0)
    entry_zone_atr: float = Field(ge=0)

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if self.entry_zone_atr >= self.stop_atr_multiplier:
            raise ValueError("entry_zone_atr must be narrower than stop_atr_multiplier")
        if self.take_profit_rr * self.stop_atr_multiplier <= self.entry_zone_atr:
            raise ValueError("the take-profit must lie beyond the entry zone")
        return self


class Momentum(Strategy[MomentumParameters]):
    """Trade a displacement of at least ``threshold_atr`` ATR over ``momentum_period`` bars."""

    strategy_id = "momentum"
    parameters_model = MomentumParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        parameters = self.parameters
        timeframe = context.primary_timeframe
        closes = context.closes(timeframe)
        if len(closes) <= parameters.momentum_period:
            return None
        volatility = atr(
            context.highs(timeframe), context.lows(timeframe), closes, parameters.atr_period
        )[-1]
        if volatility is None or volatility <= 0:
            return None
        change = closes[-1] - closes[-1 - parameters.momentum_period]
        strength = change / volatility
        if strength >= parameters.threshold_atr:
            direction, side = Direction.BUY, "up"
        elif strength <= -parameters.threshold_atr:
            direction, side = Direction.SELL, "down"
        else:
            return None
        return _signal(
            direction=direction,
            close=closes[-1],
            volatility=volatility,
            stop_atr_multiplier=parameters.stop_atr_multiplier,
            take_profit_rr=parameters.take_profit_rr,
            entry_zone_atr=parameters.entry_zone_atr,
            reason=(
                f"momentum {change:+.6f} over {parameters.momentum_period} bars = "
                f"{strength:+.2f} ATR {side}; target {parameters.take_profit_rr}R"
            ),
            indicators={"momentum": change, "momentum_atr": strength, "atr": volatility},
        )


class VolatilityBreakoutParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    atr_period: int = Field(ge=1)
    breakout_atr: float = Field(gt=0)
    stop_atr_multiplier: float = Field(gt=0)
    take_profit_rr: float = Field(gt=0)
    entry_zone_atr: float = Field(ge=0)

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if self.entry_zone_atr >= self.stop_atr_multiplier:
            raise ValueError("entry_zone_atr must be narrower than stop_atr_multiplier")
        if self.take_profit_rr * self.stop_atr_multiplier <= self.entry_zone_atr:
            raise ValueError("the take-profit must lie beyond the entry zone")
        return self


class VolatilityBreakout(Strategy[VolatilityBreakoutParameters]):
    """Trade a single-bar expansion: the close jumps more than ``breakout_atr`` ATR."""

    strategy_id = "volatility_breakout"
    parameters_model = VolatilityBreakoutParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        parameters = self.parameters
        timeframe = context.primary_timeframe
        closes = context.closes(timeframe)
        if len(closes) < 2:
            return None
        volatility = atr(
            context.highs(timeframe), context.lows(timeframe), closes, parameters.atr_period
        )[-1]
        if volatility is None or volatility <= 0:
            return None
        expansion = (closes[-1] - closes[-2]) / volatility
        if expansion >= parameters.breakout_atr:
            direction, side = Direction.BUY, "up"
        elif expansion <= -parameters.breakout_atr:
            direction, side = Direction.SELL, "down"
        else:
            return None
        return _signal(
            direction=direction,
            close=closes[-1],
            volatility=volatility,
            stop_atr_multiplier=parameters.stop_atr_multiplier,
            take_profit_rr=parameters.take_profit_rr,
            entry_zone_atr=parameters.entry_zone_atr,
            reason=(
                f"close moved {expansion:+.2f} ATR {side} in one bar; stop "
                f"{parameters.stop_atr_multiplier} x ATR{parameters.atr_period}"
            ),
            indicators={"expansion_atr": expansion, "atr": volatility},
        )


class ConsensusEnsembleParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ema_fast: int = Field(ge=2)
    ema_slow: int = Field(ge=3)
    rsi_period: int = Field(ge=2)
    rsi_bull: float = Field(gt=0, lt=100)
    rsi_bear: float = Field(gt=0, lt=100)
    atr_period: int = Field(ge=1)
    stop_atr_multiplier: float = Field(gt=0)
    take_profit_rr: float = Field(gt=0)
    entry_zone_atr: float = Field(ge=0)

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if self.ema_slow <= self.ema_fast:
            raise ValueError("ema_slow must be longer than ema_fast")
        if self.rsi_bear >= self.rsi_bull:
            raise ValueError("rsi_bear must be below rsi_bull")
        if self.entry_zone_atr >= self.stop_atr_multiplier:
            raise ValueError("entry_zone_atr must be narrower than stop_atr_multiplier")
        if self.take_profit_rr * self.stop_atr_multiplier <= self.entry_zone_atr:
            raise ValueError("the take-profit must lie beyond the entry zone")
        return self


class ConsensusEnsemble(Strategy[ConsensusEnsembleParameters]):
    """An ensemble of two independent rules: a trend EMA and an RSI must agree.

    Neither indicator alone opens a trade. This is the "combinaisons d'indicateurs /
    ensembles" family: a vote, not a single condition.
    """

    strategy_id = "consensus_ensemble"
    parameters_model = ConsensusEnsembleParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        parameters = self.parameters
        timeframe = context.primary_timeframe
        closes = context.closes(timeframe)
        fast = ema(closes, parameters.ema_fast)
        slow = ema(closes, parameters.ema_slow)
        reading = (
            rsi(closes, parameters.rsi_period)[-1] if len(closes) > parameters.rsi_period else None
        )
        volatility = atr(
            context.highs(timeframe), context.lows(timeframe), closes, parameters.atr_period
        )[-1]
        fast_now, slow_now = fast[-1], slow[-1]
        if reading is None or fast_now is None or slow_now is None or volatility is None:
            return None
        if volatility <= 0:
            return None
        close = closes[-1]
        bullish = fast_now > slow_now and close > slow_now and reading >= parameters.rsi_bull
        bearish = fast_now < slow_now and close < slow_now and reading <= parameters.rsi_bear
        if bullish:
            direction, side = Direction.BUY, "bullish consensus"
        elif bearish:
            direction, side = Direction.SELL, "bearish consensus"
        else:
            return None
        return _signal(
            direction=direction,
            close=close,
            volatility=volatility,
            stop_atr_multiplier=parameters.stop_atr_multiplier,
            take_profit_rr=parameters.take_profit_rr,
            entry_zone_atr=parameters.entry_zone_atr,
            reason=(
                f"{side}: EMA{parameters.ema_fast}/EMA{parameters.ema_slow} and "
                f"RSI{parameters.rsi_period}={reading:.1f}; target {parameters.take_profit_rr}R"
            ),
            indicators={
                "ema_fast": fast_now,
                "ema_slow": slow_now,
                "rsi": reading,
                "atr": volatility,
            },
        )


# --------------------------------------------------------------------------------------
# Templates: pure functions from a parameter grid to candidate manifests.
# --------------------------------------------------------------------------------------

Grid = Mapping[str, Sequence[float]]
ParameterGrid = Mapping[str, Grid]


@dataclass(frozen=True)
class TemplateScope:
    """What a template may know: the symbols, the timeframe and the candidate version."""

    symbols: tuple[str, ...]
    timeframe: Timeframe
    version: str = DEFAULT_VERSION


@dataclass(frozen=True)
class CandidateProposal:
    """One candidate before any candle is read: a manifest, parameters and a factory."""

    family: str
    strategy_id: str
    manifest: StrategyManifest
    parameters: Mapping[str, float]
    factory: Callable[[Mapping[str, float]], Strategy[Any]]
    label: str = ""


TemplateFunction = Callable[[TemplateScope, Grid], Iterator[CandidateProposal]]


@dataclass(frozen=True)
class FamilyTemplate:
    family: str
    description: str
    template: TemplateFunction


def grid_values(grid: Grid, key: str, default: Sequence[float]) -> tuple[float, ...]:
    raw = grid.get(key)
    return tuple(float(value) for value in (raw if raw else default))


def _whole(value: float) -> int:
    return round(value)


def _history_bars(lookback: int) -> int:
    return min(10_000, max(30, lookback + 30))


def _factory(
    model: type[BaseModel], strategy_class: type[Strategy[Any]]
) -> Callable[[Mapping[str, float]], Strategy[Any]]:
    def build(parameters: Mapping[str, float]) -> Strategy[Any]:
        return strategy_class(model.model_validate(dict(parameters)))

    return build


def build_proposal(
    scope: TemplateScope,
    family: str,
    strategy_class: type[Strategy[Any]],
    parameters: Mapping[str, float],
    *,
    lookback: int,
) -> CandidateProposal:
    strategy_id = strategy_class.strategy_id
    # Integer periods stay integers: `protocol.perturb_parameters` keeps an integral
    # parameter integral (a period of 15.4 is not a strategy), and it can only do so if the
    # template did not silently turn the period into a float first.
    clean: dict[str, float] = {key: value for key, value in sorted(parameters.items())}
    manifest = StrategyManifest(
        strategy_id=strategy_id,
        version=scope.version,
        max_mode=TradingMode.SIGNAL,
        allowed_symbols=scope.symbols,
        timeframes=(scope.timeframe,),
        history_bars=_history_bars(lookback),
        expiry_bars=2,
        parameters=dict(clean),
    )
    return CandidateProposal(
        family=family,
        strategy_id=strategy_id,
        manifest=manifest,
        parameters=clean,
        factory=_factory(strategy_class.parameters_model, strategy_class),
    )


def trend_following_template(scope: TemplateScope, grid: Grid) -> Iterator[CandidateProposal]:
    """Existing family: the reference EMA crossover (:class:`Witness`)."""
    fasts = grid_values(grid, "ema_fast", (10.0, 20.0))
    slows = grid_values(grid, "ema_slow", (30.0, 60.0))
    targets = grid_values(grid, "take_profit_rr", (1.5, 2.5))
    stop = grid_values(grid, "stop_atr_multiplier", (1.5,))[0]
    zone = grid_values(grid, "entry_zone_atr", (0.1,))[0]
    atr_period = _whole(grid_values(grid, "atr_period", (14.0,))[0])
    for fast, slow, take_profit_rr in product(fasts, slows, targets):
        if _whole(slow) <= _whole(fast):
            continue
        yield build_proposal(
            scope,
            "trend_following",
            Witness,
            {
                "ema_fast": _whole(fast),
                "ema_slow": _whole(slow),
                "atr_period": atr_period,
                "stop_atr_multiplier": stop,
                "take_profit_rr": take_profit_rr,
                "entry_zone_atr": zone,
            },
            lookback=_whole(slow),
        )


def momentum_template(scope: TemplateScope, grid: Grid) -> Iterator[CandidateProposal]:
    """Family: trade the size of a move, normalised by volatility."""
    periods = grid_values(grid, "momentum_period", (10.0, 20.0))
    thresholds = grid_values(grid, "threshold_atr", (1.0,))
    targets = grid_values(grid, "take_profit_rr", (2.0,))
    stop = grid_values(grid, "stop_atr_multiplier", (1.5,))[0]
    zone = grid_values(grid, "entry_zone_atr", (0.1,))[0]
    atr_period = _whole(grid_values(grid, "atr_period", (14.0,))[0])
    for period, threshold, take_profit_rr in product(periods, thresholds, targets):
        yield build_proposal(
            scope,
            "momentum",
            Momentum,
            {
                "momentum_period": _whole(period),
                "threshold_atr": threshold,
                "atr_period": atr_period,
                "stop_atr_multiplier": stop,
                "take_profit_rr": take_profit_rr,
                "entry_zone_atr": zone,
            },
            lookback=_whole(period) + atr_period,
        )


def mean_reversion_template(scope: TemplateScope, grid: Grid) -> Iterator[CandidateProposal]:
    """Family: buy exhaustion, sell euphoria (RSI bands)."""
    periods = grid_values(grid, "rsi_period", (14.0, 21.0))
    oversolds = grid_values(grid, "oversold", (30.0,))
    overboughts = grid_values(grid, "overbought", (70.0,))
    targets = grid_values(grid, "take_profit_rr", (2.0,))
    stop = grid_values(grid, "stop_atr_multiplier", (1.5,))[0]
    zone = grid_values(grid, "entry_zone_atr", (0.1,))[0]
    atr_period = _whole(grid_values(grid, "atr_period", (14.0,))[0])
    for period, oversold, overbought, take_profit_rr in product(
        periods, oversolds, overboughts, targets
    ):
        if oversold >= overbought:
            continue
        yield build_proposal(
            scope,
            "mean_reversion",
            MeanReversion,
            {
                "rsi_period": _whole(period),
                "oversold": oversold,
                "overbought": overbought,
                "atr_period": atr_period,
                "stop_atr_multiplier": stop,
                "take_profit_rr": take_profit_rr,
                "entry_zone_atr": zone,
            },
            lookback=max(_whole(period), atr_period),
        )


def breakout_template(scope: TemplateScope, grid: Grid) -> Iterator[CandidateProposal]:
    """Existing family: the Donchian channel breakout (:class:`TrendBreakout`)."""
    channels = grid_values(grid, "channel_period", (20.0, 40.0))
    trends = grid_values(grid, "trend_period", (50.0,))
    targets = grid_values(grid, "take_profit_rr", (2.0,))
    stop = grid_values(grid, "stop_atr_multiplier", (1.5,))[0]
    zone = grid_values(grid, "entry_zone_atr", (0.1,))[0]
    atr_period = _whole(grid_values(grid, "atr_period", (14.0,))[0])
    for channel, trend, take_profit_rr in product(channels, trends, targets):
        yield build_proposal(
            scope,
            "breakout",
            TrendBreakout,
            {
                "channel_period": _whole(channel),
                "trend_period": _whole(trend),
                "atr_period": atr_period,
                "stop_atr_multiplier": stop,
                "take_profit_rr": take_profit_rr,
                "entry_zone_atr": zone,
            },
            lookback=max(_whole(channel), _whole(trend)) + 1,
        )


def volatility_breakout_template(scope: TemplateScope, grid: Grid) -> Iterator[CandidateProposal]:
    """Family: a single-bar range expansion, whatever its direction."""
    expansions = grid_values(grid, "breakout_atr", (0.5, 1.0))
    targets = grid_values(grid, "take_profit_rr", (2.0,))
    stop = grid_values(grid, "stop_atr_multiplier", (1.5,))[0]
    zone = grid_values(grid, "entry_zone_atr", (0.1,))[0]
    atr_period = _whole(grid_values(grid, "atr_period", (14.0,))[0])
    for expansion, take_profit_rr in product(expansions, targets):
        yield build_proposal(
            scope,
            "volatility_breakout",
            VolatilityBreakout,
            {
                "atr_period": atr_period,
                "breakout_atr": expansion,
                "stop_atr_multiplier": stop,
                "take_profit_rr": take_profit_rr,
                "entry_zone_atr": zone,
            },
            lookback=atr_period + 2,
        )


def ensemble_template(scope: TemplateScope, grid: Grid) -> Iterator[CandidateProposal]:
    """Family: two rules must agree before a trade exists (trend EMA vote + RSI vote)."""
    fasts = grid_values(grid, "ema_fast", (10.0, 20.0))
    slows = grid_values(grid, "ema_slow", (30.0, 60.0))
    bulls = grid_values(grid, "rsi_bull", (55.0,))
    bears = grid_values(grid, "rsi_bear", (45.0,))
    targets = grid_values(grid, "take_profit_rr", (2.0,))
    rsi_period = _whole(grid_values(grid, "rsi_period", (14.0,))[0])
    stop = grid_values(grid, "stop_atr_multiplier", (1.5,))[0]
    zone = grid_values(grid, "entry_zone_atr", (0.1,))[0]
    atr_period = _whole(grid_values(grid, "atr_period", (14.0,))[0])
    for fast, slow, bull, bear, take_profit_rr in product(fasts, slows, bulls, bears, targets):
        if _whole(slow) <= _whole(fast) or bear >= bull:
            continue
        yield build_proposal(
            scope,
            "ensemble",
            ConsensusEnsemble,
            {
                "ema_fast": _whole(fast),
                "ema_slow": _whole(slow),
                "rsi_period": rsi_period,
                "rsi_bull": bull,
                "rsi_bear": bear,
                "atr_period": atr_period,
                "stop_atr_multiplier": stop,
                "take_profit_rr": take_profit_rr,
                "entry_zone_atr": zone,
            },
            lookback=max(_whole(slow), rsi_period, atr_period),
        )


FAMILIES: tuple[FamilyTemplate, ...] = (
    FamilyTemplate(
        "trend_following", "suivi de tendance (croisement EMA)", trend_following_template
    ),
    FamilyTemplate("momentum", "momentum normalisé par l'ATR", momentum_template),
    FamilyTemplate("mean_reversion", "retour à la moyenne (RSI)", mean_reversion_template),
    FamilyTemplate("breakout", "cassure de canal Donchian", breakout_template),
    FamilyTemplate(
        "volatility_breakout",
        "cassure de volatilité (expansion ATR)",
        volatility_breakout_template,
    ),
    FamilyTemplate("ensemble", "ensemble : consensus tendance + RSI", ensemble_template),
)


def default_grid() -> dict[str, dict[str, tuple[float, ...]]]:
    """A small, deliberately modest default grid: breadth first, depth later."""
    return {
        "trend_following": {
            "ema_fast": (10.0, 20.0),
            "ema_slow": (30.0, 60.0),
            "take_profit_rr": (1.5, 2.5),
        },
        "momentum": {"momentum_period": (10.0, 20.0), "threshold_atr": (1.0,)},
        "mean_reversion": {"rsi_period": (14.0, 21.0), "oversold": (30.0,), "overbought": (70.0,)},
        "breakout": {"channel_period": (20.0, 40.0), "trend_period": (50.0,)},
        "volatility_breakout": {"breakout_atr": (0.5, 1.0)},
        "ensemble": {"ema_fast": (10.0,), "ema_slow": (30.0,), "rsi_bull": (55.0,)},
    }


# --------------------------------------------------------------------------------------
# Protocol settings and the report.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscoveryProtocol:
    """The anti-overfitting settings every candidate crosses, and the cost assumptions."""

    token: str = "discovery:sealed-holdout"  # noqa: S105 - a seal token, not a credential
    train_fraction: float = 0.6
    validation_fraction: float = 0.2
    perturbation: float = 0.1
    min_trades: int = MIN_TRADES_FOR_STABILITY
    min_walk_forward_ratio: float = DEFAULT_WALK_FORWARD_RATIO
    max_parameter_dispersion: float = PARAMETER_CV_MAX
    min_stability_score: float = STABILITY_SCORE_MIN
    min_profitable_regime_ratio: float = PROFITABLE_REGIME_MIN
    min_out_of_sample_retention: float = OOS_RETENTION_MIN
    walk_forward: WalkForwardPlan = field(
        default_factory=lambda: WalkForwardPlan(
            train_bars=250, validation_bars=100, step_bars=150, max_folds=6
        )
    )
    spread_fraction: float = 5e-5
    slippage_fraction: float = 2e-5
    commission: Decimal = Decimal("0.5")
    risk_eur: Decimal = Decimal("10")
    #: Tolerated false-discovery rate of the selection, and the resolution of the Monte-Carlo
    #: null used to price each survivor of the ladder.
    false_discovery_rate: float = DEFAULT_FALSE_DISCOVERY_RATE
    monte_carlo_iterations: int = DEFAULT_MONTE_CARLO_ITERATIONS

    def __post_init__(self) -> None:
        if not self.token:
            raise ValueError("a seal token is required; an unlocked holdout is not a holdout")
        if not 0 < self.train_fraction < 1 or not 0 < self.validation_fraction < 1:
            raise ValueError("train and validation fractions must be in (0, 1)")
        if self.train_fraction + self.validation_fraction >= 1:
            raise ValueError("train and validation must leave a sealed holdout")
        if self.perturbation <= 0:
            raise ValueError("perturbation must be positive")
        if self.min_trades < 1:
            raise ValueError("min_trades must be at least 1")
        if not 0 <= self.min_walk_forward_ratio <= 1:
            raise ValueError("min_walk_forward_ratio must be in [0, 1]")
        if not 0 <= self.min_stability_score <= 1:
            raise ValueError("min_stability_score must be in [0, 1]")
        if not 0 < self.false_discovery_rate <= 1:
            raise ValueError("false_discovery_rate must be in (0, 1]")
        if self.monte_carlo_iterations < 1:
            raise ValueError("monte_carlo_iterations must be positive")
        if self.risk_eur <= 0:
            raise ValueError("risk_eur must be positive")

    def config_for(self, dataset: CandleDataset) -> BacktestConfig:
        """Costs proportional to the price of the market, so bp stay comparable."""
        price = dataset.candles[0].close
        return BacktestConfig(
            symbol=dataset.symbol,
            costs=CostModel(
                spread=round(price * self.spread_fraction, 8),
                slippage_fixed=round(price * self.slippage_fraction, 8),
                commission_per_trade=self.commission,
            ),
            risk_eur=self.risk_eur,
            mode=TradingMode.SIGNAL,
        )


@dataclass(frozen=True)
class WalkForwardOutcome:
    """How a candidate behaved on the rolling origin, folds included."""

    folds: int
    profitable_folds: int
    trades: int
    net_profit: Decimal
    skipped_folds: int = 0

    @property
    def ratio(self) -> float:
        return self.profitable_folds / self.folds if self.folds else 0.0


@dataclass(frozen=True)
class CandidateOutcome:
    """One (candidate, market) evaluation and exactly one verdict."""

    market: str
    family: str
    label: str
    strategy_id: str
    version: str
    parameters: Mapping[str, float]
    retained: bool
    cause: DiscardCause | None
    detail: str = ""
    train: Performance | None = None
    validation: Performance | None = None
    out_of_sample: Performance | None = None
    walk_forward: WalkForwardOutcome | None = None
    stability: StabilityReport | None = None
    invalid_perturbations: int = 0
    strategy_errors: tuple[str, ...] = ()
    p_value: float | None = None

    @property
    def holdout_was_read(self) -> bool:
        return self.out_of_sample is not None


@dataclass(frozen=True)
class MarketOverview:
    """One dataset and how it was cut. `skipped` explains an unusable dataset."""

    market: str
    dataset_id: str
    fingerprint: str
    bars: int
    train_bars: int
    validation_bars: int
    holdout_bars: int
    walk_forward_folds: int
    holdout_unlocks: int
    skipped: str | None = None

    @property
    def usable(self) -> bool:
        return self.skipped is None


@dataclass(frozen=True)
class FamilySummary:
    """The honest scoreboard: tested, retained before/after correction, one line per cause.

    ``retained`` is the count *after* the false-discovery correction -- what the family can
    actually claim. ``retained_before_correction`` is how many survived the protocol alone,
    which is what makes the cost of the multiple-testing correction visible.
    """

    family: str
    description: str
    tested: int
    retained: int
    failures: Mapping[DiscardCause, int]
    retained_before_correction: int = 0
    expected_false_discoveries: float = 0.0

    @property
    def discarded(self) -> int:
        return self.tested - self.retained


@dataclass(frozen=True)
class MultipleTestingReport:
    """What the false-discovery control did to one discovery run.

    ``hypotheses`` is the number of candidates the laboratory tried, not the number that
    reached the sealed set: that is the number of attempts the correction must pay for.
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
class DiscoveryReport:
    """Everything a discovery run produced, deterministically and without a wall clock."""

    markets: tuple[MarketOverview, ...]
    families: tuple[FamilySummary, ...]
    candidates: tuple[CandidateOutcome, ...]
    protocol: DiscoveryProtocol
    multiple_testing: MultipleTestingReport

    def retained(self) -> tuple[CandidateOutcome, ...]:
        return tuple(candidate for candidate in self.candidates if candidate.retained)

    def discarded(self) -> tuple[CandidateOutcome, ...]:
        return tuple(candidate for candidate in self.candidates if not candidate.retained)

    def family(self, family: str) -> FamilySummary | None:
        return next((item for item in self.families if item.family == family), None)

    def failures_by_cause(self) -> dict[DiscardCause, int]:
        totals: dict[DiscardCause, int] = {}
        for item in self.families:
            for cause, count in item.failures.items():
                totals[cause] = totals.get(cause, 0) + count
        return totals

    def holdout_unlocks(self) -> int:
        return sum(market.holdout_unlocks for market in self.markets)

    def to_dict(self) -> dict[str, Any]:
        return report_to_dict(self)


def report_to_dict(report: DiscoveryReport) -> dict[str, Any]:
    """A JSON-ready view of the report. No timestamp: the report is deterministic."""
    return {
        "protocol": {
            "train_fraction": report.protocol.train_fraction,
            "validation_fraction": report.protocol.validation_fraction,
            "perturbation": report.protocol.perturbation,
            "min_trades": report.protocol.min_trades,
            "min_walk_forward_ratio": report.protocol.min_walk_forward_ratio,
            "max_parameter_dispersion": report.protocol.max_parameter_dispersion,
            "min_stability_score": report.protocol.min_stability_score,
            "min_profitable_regime_ratio": report.protocol.min_profitable_regime_ratio,
            "min_out_of_sample_retention": report.protocol.min_out_of_sample_retention,
            "false_discovery_rate": report.protocol.false_discovery_rate,
            "monte_carlo_iterations": report.protocol.monte_carlo_iterations,
            "walk_forward": {
                "train_bars": report.protocol.walk_forward.train_bars,
                "validation_bars": report.protocol.walk_forward.validation_bars,
                "step_bars": report.protocol.walk_forward.step_bars,
                "max_folds": report.protocol.walk_forward.max_folds,
            },
            "costs": {
                "spread_fraction": report.protocol.spread_fraction,
                "slippage_fraction": report.protocol.slippage_fraction,
                "commission": str(report.protocol.commission),
            },
        },
        "markets": [
            {
                "market": market.market,
                "dataset_id": market.dataset_id,
                "fingerprint": market.fingerprint,
                "bars": market.bars,
                "train_bars": market.train_bars,
                "validation_bars": market.validation_bars,
                "holdout_bars": market.holdout_bars,
                "walk_forward_folds": market.walk_forward_folds,
                "holdout_unlocks": market.holdout_unlocks,
                "skipped": market.skipped,
            }
            for market in report.markets
        ],
        "families": [
            {
                "family": item.family,
                "description": item.description,
                "tested": item.tested,
                "retained_before_correction": item.retained_before_correction,
                "retained": item.retained,
                "discarded": item.discarded,
                "expected_false_discoveries": item.expected_false_discoveries,
                "failures": [
                    {"cause": cause.value, "count": count, "description": cause.description}
                    for cause, count in sorted(
                        item.failures.items(), key=lambda pair: list(DiscardCause).index(pair[0])
                    )
                ],
            }
            for item in report.families
        ],
        "multiple_testing": {
            "method": report.multiple_testing.method,
            "alpha": report.multiple_testing.alpha,
            "expected_false_discovery_rate": report.multiple_testing.expected_false_discovery_rate,
            "expected_false_discoveries": report.multiple_testing.expected_false_discoveries,
            "hypotheses": report.multiple_testing.hypotheses,
            "bonferroni_threshold": report.multiple_testing.bonferroni_threshold,
            "discoveries_before": report.multiple_testing.discoveries_before,
            "discoveries_after": report.multiple_testing.discoveries_after,
            "rejected_by_correction": report.multiple_testing.rejected_by_correction,
        },
        "totals": {
            "tested": len(report.candidates),
            "retained_before_correction": report.multiple_testing.discoveries_before,
            "retained": len(report.retained()),
            "discarded": len(report.discarded()),
            "failures": [
                {"cause": cause.value, "count": count, "description": cause.description}
                for cause, count in sorted(
                    report.failures_by_cause().items(),
                    key=lambda pair: list(DiscardCause).index(pair[0]),
                )
            ],
        },
        "candidates": [_candidate_to_dict(candidate) for candidate in report.candidates],
    }


def _candidate_to_dict(candidate: CandidateOutcome) -> dict[str, Any]:
    return {
        "market": candidate.market,
        "family": candidate.family,
        "label": candidate.label,
        "strategy_id": candidate.strategy_id,
        "version": candidate.version,
        "retained": candidate.retained,
        "cause": None if candidate.cause is None else candidate.cause.value,
        "detail": candidate.detail,
        "p_value": candidate.p_value,
        "parameters": {key: float(value) for key, value in candidate.parameters.items()},
        "train_net_profit": _money(candidate.train),
        "validation_net_profit": _money(candidate.validation),
        "out_of_sample_net_profit": _money(candidate.out_of_sample),
        "train_trades": None if candidate.train is None else candidate.train.trades,
        "out_of_sample_trades": (
            None if candidate.out_of_sample is None else candidate.out_of_sample.trades
        ),
        "walk_forward_folds": None
        if candidate.walk_forward is None
        else candidate.walk_forward.folds,
        "walk_forward_profitable_folds": (
            None if candidate.walk_forward is None else candidate.walk_forward.profitable_folds
        ),
        "walk_forward_ratio": None
        if candidate.walk_forward is None
        else candidate.walk_forward.ratio,
        "walk_forward_skipped_folds": (
            None if candidate.walk_forward is None else candidate.walk_forward.skipped_folds
        ),
        "holdout_was_read": candidate.holdout_was_read,
        "invalid_perturbations": candidate.invalid_perturbations,
        "strategy_errors": list(candidate.strategy_errors),
        "stability": (
            None
            if candidate.stability is None
            else {
                "score": candidate.stability.score,
                "out_of_sample_retention": candidate.stability.out_of_sample_retention,
                "parameter_dispersion": candidate.stability.parameter_dispersion,
                "profitable_regime_ratio": candidate.stability.profitable_regime_ratio,
                "trades": candidate.stability.trades,
                "fragile": candidate.stability.fragile,
                "reasons": list(candidate.stability.reasons),
            }
        ),
    }


def _money(performance: Performance | None) -> str | None:
    return None if performance is None else str(performance.net_profit)


# --------------------------------------------------------------------------------------
# The discard ladder: one place decides why a candidate is not retained.
# --------------------------------------------------------------------------------------


def early_discard_cause(
    stability: StabilityReport,
    walk_forward_outcome: WalkForwardOutcome,
    *,
    in_sample_trades: int,
    protocol: DiscoveryProtocol,
    invalid_perturbations: int = 0,
) -> DiscardCause | None:
    """Gates that roll-forward evidence can decide, before the sealed set is touched."""
    if in_sample_trades < protocol.min_trades:
        return DiscardCause.TOO_FEW_TRADES
    if walk_forward_outcome.folds < 1:
        return DiscardCause.OVERFITTING
    if walk_forward_outcome.ratio < protocol.min_walk_forward_ratio:
        return DiscardCause.OVERFITTING
    if invalid_perturbations > 0:
        return DiscardCause.PARAMETER_DISPERSION
    if stability.parameter_dispersion > protocol.max_parameter_dispersion:
        return DiscardCause.PARAMETER_DISPERSION
    if stability.score < protocol.min_stability_score:
        return DiscardCause.UNSTABLE
    if stability.profitable_regime_ratio < protocol.min_profitable_regime_ratio:
        return DiscardCause.UNSTABLE
    return None


def discard_cause(
    stability: StabilityReport,
    walk_forward_outcome: WalkForwardOutcome,
    out_of_sample: Performance,
    retention: float,
    *,
    in_sample_trades: int,
    protocol: DiscoveryProtocol,
    invalid_perturbations: int = 0,
) -> DiscardCause | None:
    """The full ladder, including the sealed out-of-sample confirmation."""
    early = early_discard_cause(
        stability,
        walk_forward_outcome,
        in_sample_trades=in_sample_trades,
        protocol=protocol,
        invalid_perturbations=invalid_perturbations,
    )
    if early is not None:
        return early
    if out_of_sample.net_profit <= 0:
        return DiscardCause.OUT_OF_SAMPLE_NEGATIVE
    if retention < protocol.min_out_of_sample_retention:
        return DiscardCause.OUT_OF_SAMPLE_NEGATIVE
    return None


# --------------------------------------------------------------------------------------
# False-discovery control: a survivor of fifty-one attempts is not yet a discovery.
# --------------------------------------------------------------------------------------
#
# Every candidate of a run is one hypothesis tested ("this candidate has no edge"). Keeping
# the one that looks best out of many and calling it a discovery is the classic multiple-
# comparison trap: with enough attempts, chance alone produces a presentable survivor. The
# ladder above judges each candidate *alone*; the functions below judge the *selection*.
#
# The honest difficulty is that the existing protocol produces no p-value: `stability_report`
# is a robustness score, and `protocol.monte_carlo` resamples the realized P&L, which centres
# on the observed total and therefore answers "what other orderings of these same trades?"
# rather than "what would a rule without edge have done?". So the p-value is built here,
# explicitly, and its assumptions are written down rather than implied.


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
    * an empty out-of-sample sample returns ``1.0``: no trade is no evidence of an edge.

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


def control_false_discoveries(
    candidates: Sequence[CandidateOutcome],
    *,
    alpha: float = DEFAULT_FALSE_DISCOVERY_RATE,
) -> tuple[tuple[CandidateOutcome, ...], MultipleTestingReport]:
    """Demote the survivors that do not clear the false-discovery correction.

    Every candidate of the run is one test. The ones that never reached the sealed set carry
    no out-of-sample evidence and are counted with ``p = 1``; that keeps the number of tests
    equal to the number of candidates the laboratory actually tried, which makes the
    correction *conservative* rather than flattering. A demoted candidate keeps its p-value
    and receives :attr:`DiscardCause.FALSE_DISCOVERY`, never a silent deletion.
    """
    count = len(candidates)
    p_values = [1.0 if candidate.p_value is None else candidate.p_value for candidate in candidates]
    survives = benjamini_hochberg(p_values, alpha=alpha)
    # A demoted candidate is quoted the threshold *its own rank* required, ``rank/m * alpha``:
    # Benjamini-Hochberg rejects exactly the p-values above their rank threshold, so the line
    # says what the candidate failed to clear without borrowing another candidate's number.
    ranks = [1 + sum(1 for other in p_values if other < value) for value in p_values]
    controlled: list[CandidateOutcome] = []
    demoted = 0
    for position, (candidate, p_value, significant) in enumerate(
        zip(candidates, p_values, survives, strict=True)
    ):
        if candidate.retained and not significant:
            demoted += 1
            controlled.append(
                replace(
                    candidate,
                    retained=False,
                    cause=DiscardCause.FALSE_DISCOVERY,
                    detail=(
                        f"p={p_value:.4f} > seuil de Benjamini-Hochberg "
                        f"{ranks[position] / count * alpha:.4f} au rang {ranks[position]} "
                        f"sur {count} test(s) à alpha={alpha:.2f}"
                    ),
                )
            )
            continue
        controlled.append(candidate)
    before = sum(1 for candidate in candidates if candidate.retained)
    return tuple(controlled), MultipleTestingReport(
        method="benjamini_hochberg",
        alpha=alpha,
        hypotheses=count,
        discoveries_before=before,
        discoveries_after=before - demoted,
        rejected_by_correction=demoted,
        bonferroni_threshold=bonferroni_threshold(count, alpha=alpha),
        expected_false_discoveries=count * alpha,
    )


def _candidate_seed(market: str, family: str, label: str) -> int:
    """A stable per-candidate Monte-Carlo seed.

    ``hash()`` is salted per process, so it would make two runs of the same discovery
    disagree. CRC-32 of the candidate's identity is stable across platforms and processes.
    """
    return crc32(f"{market}|{family}|{label}".encode())


# --------------------------------------------------------------------------------------
# The discovery run.
# --------------------------------------------------------------------------------------


def discover(
    datasets: Mapping[str, CandleDataset],
    grid: ParameterGrid,
    protocol: DiscoveryProtocol | None = None,
    *,
    families: Sequence[FamilyTemplate] = FAMILIES,
) -> DiscoveryReport:
    """Explore every family on every dataset and report the survivors *and* the failures.

    `families` is the extension point: a test or a future laboratory can add its own
    template without this module learning about it. An empty dataset mapping is not an
    error: it produces an empty report so a caller can always render something.
    """
    settings = protocol if protocol is not None else DiscoveryProtocol()
    if not datasets:
        empty, multiple_testing = control_false_discoveries((), alpha=settings.false_discovery_rate)
        return DiscoveryReport(
            markets=(),
            families=_family_summaries((), families, alpha=settings.false_discovery_rate),
            candidates=empty,
            protocol=settings,
            multiple_testing=multiple_testing,
        )
    symbols = tuple(sorted({dataset.symbol for dataset in datasets.values()}))
    markets: list[MarketOverview] = []
    candidates: list[CandidateOutcome] = []
    for market, dataset in sorted(datasets.items()):
        outcomes, overview = _run_market(market, dataset, symbols, grid, settings, families)
        candidates.extend(outcomes)
        markets.append(overview)
    # The correction comes last, over the *whole* run: the selection spans every family and
    # every market, so the number of tests is the number of candidates the run tried.
    controlled, multiple_testing = control_false_discoveries(
        tuple(candidates), alpha=settings.false_discovery_rate
    )
    return DiscoveryReport(
        markets=tuple(markets),
        families=_family_summaries(controlled, families, alpha=settings.false_discovery_rate),
        candidates=controlled,
        protocol=settings,
        multiple_testing=multiple_testing,
    )


def _run_market(
    market: str,
    dataset: CandleDataset,
    symbols: tuple[str, ...],
    grid: ParameterGrid,
    settings: DiscoveryProtocol,
    families: Sequence[FamilyTemplate],
) -> tuple[list[CandidateOutcome], MarketOverview]:
    try:
        split = split_dataset(
            dataset,
            token=_market_token(settings, market),
            train_fraction=settings.train_fraction,
            validation_fraction=settings.validation_fraction,
        )
    except ValueError as error:
        return [], _skipped_market(market, dataset, str(error))
    rolling = tuple(split.train) + tuple(split.validation)
    folds = walk_forward(rolling, settings.walk_forward)
    if not folds:
        return [], _skipped_market(
            market,
            dataset,
            f"no walk-forward fold fits in {len(rolling)} bars "
            f"(train {settings.walk_forward.train_bars}, "
            f"validation {settings.walk_forward.validation_bars})",
        )
    scope = TemplateScope(symbols=symbols, timeframe=dataset.timeframe)
    config = settings.config_for(dataset)
    outcomes: list[CandidateOutcome] = []
    for family in families:
        proposals = family.template(scope, grid.get(family.family, {}))
        for index, proposal in enumerate(proposals):
            outcome = _evaluate_candidate(
                market,
                dataset,
                config,
                split.train,
                split.validation,
                split.holdout,
                folds,
                proposal,
                label=proposal.label or f"{proposal.strategy_id}:{index:02d}",
                settings=settings,
                token=_market_token(settings, market),
            )
            outcomes.append(outcome)
    return outcomes, MarketOverview(
        market=market,
        dataset_id=dataset.dataset_id,
        fingerprint=dataset.fingerprint,
        bars=dataset.bars,
        train_bars=len(split.train),
        validation_bars=len(split.validation),
        holdout_bars=split.holdout.size,
        walk_forward_folds=len(folds),
        holdout_unlocks=split.holdout.unlock_count,
    )


def _market_token(settings: DiscoveryProtocol, market: str) -> str:
    return f"{settings.token}:{market}"


def _skipped_market(market: str, dataset: CandleDataset, reason: str) -> MarketOverview:
    return MarketOverview(
        market=market,
        dataset_id=dataset.dataset_id,
        fingerprint=dataset.fingerprint,
        bars=dataset.bars,
        train_bars=0,
        validation_bars=0,
        holdout_bars=0,
        walk_forward_folds=0,
        holdout_unlocks=0,
        skipped=reason,
    )


def _evaluate_candidate(
    market: str,
    dataset: CandleDataset,
    config: BacktestConfig,
    train_candles: Sequence[Candle],
    validation_candles: Sequence[Candle],
    holdout: SealedSet,
    folds: Sequence[Fold],
    proposal: CandidateProposal,
    *,
    label: str,
    settings: DiscoveryProtocol,
    token: str,
) -> CandidateOutcome:
    timeframe = dataset.timeframe
    try:
        strategy = proposal.factory(proposal.parameters)
    except Exception as error:
        return _verdict(
            market,
            proposal,
            label,
            DiscardCause.INVALID_PARAMETERS,
            detail=f"{type(error).__name__}: {error}",
        )
    try:
        train = run_backtest(strategy, proposal.manifest, {timeframe: train_candles}, config)
        validation = run_backtest(
            proposal.factory(proposal.parameters),
            proposal.manifest,
            {timeframe: validation_candles},
            config,
        )
    except ValueError as error:
        return _verdict(market, proposal, label, DiscardCause.INSUFFICIENT_DATA, detail=str(error))

    fold_performances: list[Performance] = []
    skipped_folds = 0
    for fold in folds:
        try:
            result = run_backtest(
                proposal.factory(proposal.parameters),
                proposal.manifest,
                {timeframe: fold.validation},
                config,
            )
        except ValueError:
            # A fold shorter than the manifest's history cannot judge anybody: it is counted
            # as skipped rather than silently scored as a loss.
            skipped_folds += 1
            continue
        fold_performances.append(result.performance)
    if not fold_performances:
        return _verdict(
            market,
            proposal,
            label,
            DiscardCause.INSUFFICIENT_DATA,
            detail=(
                f"no walk-forward fold holds {proposal.manifest.history_bars} history bars "
                f"({skipped_folds} fold(s) skipped)"
            ),
        )
    walk_forward_outcome = WalkForwardOutcome(
        folds=len(fold_performances),
        profitable_folds=sum(1 for item in fold_performances if item.net_profit > 0),
        trades=sum(item.trades for item in fold_performances),
        net_profit=sum((item.net_profit for item in fold_performances), Decimal(0)),
        skipped_folds=skipped_folds,
    )

    perturbations: list[Performance] = []
    invalid_perturbations = 0
    for parameters in perturb_parameters(proposal.parameters, relative=settings.perturbation):
        try:
            result = run_backtest(
                proposal.factory(parameters),
                proposal.manifest,
                {timeframe: validation_candles},
                config,
            )
        except Exception:
            invalid_perturbations += 1
            continue
        perturbations.append(result.performance)
    regimes = [regime.performance for regime in period_report(list(validation.trades))]
    stability = stability_report(
        train.performance,
        validation.performance,
        perturbations=perturbations,
        regimes=regimes,
        min_trades=settings.min_trades,
    )
    errors = tuple(sorted(set(train.strategy_errors) | set(validation.strategy_errors)))
    early = early_discard_cause(
        stability,
        walk_forward_outcome,
        in_sample_trades=train.performance.trades,
        protocol=settings,
        invalid_perturbations=invalid_perturbations,
    )
    if early is not None:
        return CandidateOutcome(
            market=market,
            family=proposal.family,
            label=label,
            strategy_id=proposal.strategy_id,
            version=proposal.manifest.version,
            parameters=proposal.parameters,
            retained=False,
            cause=early,
            detail=_first_reason(stability),
            train=train.performance,
            validation=validation.performance,
            walk_forward=walk_forward_outcome,
            stability=stability,
            invalid_perturbations=invalid_perturbations,
            strategy_errors=errors,
        )

    # The holdout trades are kept: the p-value that the selection correction needs is built
    # from them, and `confirm` is the only function allowed to unlock the sealed set. The
    # runner captures them rather than re-reading the holdout, so the audit trail (one
    # unlock per candidate) is unchanged.
    holdout_pnls: list[float] = []

    def runner(parameters: Mapping[str, float], candles: Sequence[Candle]) -> Performance:
        result = run_backtest(
            proposal.factory(parameters), proposal.manifest, {timeframe: candles}, config
        )
        holdout_pnls.extend(float(trade.pnl_eur) for trade in result.trades)
        return result.performance

    # The holdout is the last gate and the only place it is ever read: a candidate that
    # already failed on rolling evidence leaves the sealed set untouched.
    out_of_sample = confirm(runner, proposal.parameters, holdout, token)
    retention = out_of_sample_retention(train.performance, out_of_sample)
    cause = discard_cause(
        stability,
        walk_forward_outcome,
        out_of_sample,
        retention,
        in_sample_trades=train.performance.trades,
        protocol=settings,
        invalid_perturbations=invalid_perturbations,
    )
    # Only a candidate that cleared the whole ladder is priced: the others are not
    # discoveries, so a p-value would only mislead the correction. `None` means "counted as
    # p = 1", which is what `control_false_discoveries` needs.
    p_value = (
        None
        if cause is not None
        else monte_carlo_p_value(
            holdout_pnls,
            iterations=settings.monte_carlo_iterations,
            seed=_candidate_seed(market, proposal.family, label),
        )
    )
    return CandidateOutcome(
        market=market,
        family=proposal.family,
        label=label,
        strategy_id=proposal.strategy_id,
        version=proposal.manifest.version,
        parameters=proposal.parameters,
        retained=cause is None,
        cause=cause,
        detail=_first_reason(stability) if cause is DiscardCause.OUT_OF_SAMPLE_NEGATIVE else "",
        train=train.performance,
        validation=validation.performance,
        out_of_sample=out_of_sample,
        walk_forward=walk_forward_outcome,
        stability=stability,
        invalid_perturbations=invalid_perturbations,
        strategy_errors=errors,
        p_value=p_value,
    )


def _first_reason(stability: StabilityReport) -> str:
    return stability.reasons[0] if stability.reasons else ""


def _verdict(
    market: str,
    proposal: CandidateProposal,
    label: str,
    cause: DiscardCause,
    *,
    detail: str = "",
) -> CandidateOutcome:
    return CandidateOutcome(
        market=market,
        family=proposal.family,
        label=label,
        strategy_id=proposal.strategy_id,
        version=proposal.manifest.version,
        parameters=proposal.parameters,
        retained=False,
        cause=cause,
        detail=detail,
    )


def _family_summaries(
    candidates: Sequence[CandidateOutcome],
    families: Sequence[FamilyTemplate],
    *,
    alpha: float,
) -> tuple[FamilySummary, ...]:
    described = {template.family: template.description for template in families}
    ordered = [template.family for template in families]
    for candidate in candidates:
        if candidate.family not in described:
            described[candidate.family] = ""
            ordered.append(candidate.family)
    summaries: list[FamilySummary] = []
    for family in ordered:
        items = [candidate for candidate in candidates if candidate.family == family]
        failures: dict[DiscardCause, int] = {}
        for item in items:
            if item.cause is not None:
                failures[item.cause] = failures.get(item.cause, 0) + 1
        summaries.append(
            FamilySummary(
                family=family,
                description=described[family],
                tested=len(items),
                retained=sum(1 for item in items if item.retained),
                failures=failures,
                # A `false_discovery` discard is a survivor of the protocol: counting it as
                # "retained before the correction" is what makes the two columns differ.
                retained_before_correction=sum(
                    1
                    for item in items
                    if item.retained or item.cause is DiscardCause.FALSE_DISCOVERY
                ),
                expected_false_discoveries=len(items) * alpha,
            )
        )
    return tuple(summaries)
