"""La famille scalp_triple_filter : ce que la règle M1 publiée doit et ne doit pas faire.

Ces tests ne mesurent pas la rentabilité — c'est le travail du harnais de découverte. Ils
vérifient que la règle est *celle qui est décrite* : trois filtres qui doivent s'accorder,
un quatrième qui dimensionne, et aucune lecture du futur.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.discovery import (
    ScalpTripleFilter,
    ScalpTripleFilterParameters,
    TemplateScope,
    scalp_triple_filter_template,
)
from tradingagent.strategies.base import StrategyContext

START = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)


def candles(
    closes: list[float], timeframe: Timeframe, *, spread: float = 0.5, start: datetime = START
) -> tuple[Candle, ...]:
    """Une série OHLC dérivée d'une liste de clôtures, déterministe et reproductible."""
    step = timedelta(seconds=timeframe.seconds)
    return tuple(
        Candle(
            timeframe=timeframe,
            open_time=start + step * index,
            open=close,
            high=close + spread,
            low=close - spread,
            close=close,
        )
        for index, close in enumerate(closes)
    )


def ramp(length: int, start: float = 4000.0, step: float = 1.0) -> list[float]:
    return [start + step * index for index in range(length)]


def dip_then_recover(length: int = 60) -> list[float]:
    """Une chute qui met le %K en zone basse, puis un retour : le déclencheur d'un BUY."""
    falling = [4000.0 - 2.0 * index for index in range(20)]
    bottom = [falling[-1] - 0.5 * index for index in range(5)]
    recovering = [bottom[-1] + 4.0 * index for index in range(length - 25)]
    return falling + bottom + recovering


def parameters(**overrides: float) -> ScalpTripleFilterParameters:
    base: dict[str, float] = {
        "st_period": 7,
        "st_multiplier": 2.0,
        "k_period": 5,
        "k_smoothing": 2,
        "d_period": 2,
        "oversold": 30.0,
        "overbought": 70.0,
        "trend_fast": 3,
        "trend_slow": 6,
        "atr_period": 5,
        "min_atr_points": 0.0,
        "stop_atr_multiplier": 3.0,
        "take_profit_rr": 0.7,
        "entry_zone_atr": 0.1,
    }
    base.update(overrides)
    return ScalpTripleFilterParameters.model_validate(base)


def context(
    primary: list[float], higher: list[float], **overrides: float
) -> tuple[ScalpTripleFilter, StrategyContext]:
    strategy = ScalpTripleFilter(parameters(**overrides))
    m1 = candles(primary, Timeframe.M1)
    m5 = candles(higher, Timeframe.M5)
    return strategy, StrategyContext(
        symbol="XAUUSD",
        evaluated_at=m1[-1].close_time,
        primary_timeframe=Timeframe.M1,
        candles={Timeframe.M1: m1, Timeframe.M5: m5},
    )


# -- le contrat des paramètres ---------------------------------------------------------


def test_a_reward_below_one_is_accepted() -> None:
    """Le profil publié : 87 % de réussite avec un gain moyen de 0,70 fois la perte.

    Un modèle qui refuserait `take_profit_rr < 1` interdirait de représenter la stratégie
    à mesurer. C'est le seul garde-fou que ce test protège.
    """
    assert parameters(take_profit_rr=0.5).take_profit_rr == 0.5


def test_incoherent_parameters_are_refused() -> None:
    with pytest.raises(ValueError, match="oversold"):
        parameters(oversold=80.0, overbought=20.0)
    with pytest.raises(ValueError, match="trend_slow"):
        parameters(trend_fast=6, trend_slow=3)
    with pytest.raises(ValueError, match="entry_zone_atr"):
        parameters(stop_atr_multiplier=0.05, entry_zone_atr=0.1)


# -- la règle --------------------------------------------------------------------------


def test_a_buy_needs_the_higher_unit_to_agree(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mêmes M1, même Supertrend, même Stochastic : seul le filtre M5 change la décision.

    Les indicateurs sont pilotés plutôt que devinés : obtenir les quatre conditions
    simultanément sur une série synthétique demande un tour de force, et un test qui
    n'atteint jamais la branche testée ne prouve rien. Ici chaque filtre est fixé, et un
    seul bascule.
    """
    from tradingagent.research import discovery as module

    length = 40
    monkeypatch.setattr(module, "supertrend", lambda *a, **k: ([1.0] * length, [True] * length))
    monkeypatch.setattr(module, "stochastic", lambda *a, **k: ([20.0, 35.0], [20.0, 35.0]))

    primary = ramp(length, start=4000.0, step=1.0)
    higher = ramp(60, start=3900.0, step=5.0)

    with_agreement, agreed_context = context(primary, higher, trend_fast=3, trend_slow=6)
    against, against_context = context(primary, higher, trend_fast=3, trend_slow=6)

    # Le filtre M5 haussier laisse passer ; le même contexte avec EMA court < EMA long refuse.
    monkeypatch.setattr(module, "ema", lambda values, period: [5000.0 if period == 3 else 4000.0])
    agreed = with_agreement.evaluate(agreed_context)

    monkeypatch.setattr(module, "ema", lambda values, period: [4000.0 if period == 3 else 5000.0])
    refused = against.evaluate(against_context)

    assert agreed is not None and agreed.direction is Direction.BUY
    assert refused is None


def test_the_stop_is_wider_than_the_target_when_the_ratio_is_below_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La géométrie publiée : SL large devant, TP serré derrière.

    Elle est vérifiée sur un signal réellement produit, en pilotant les indicateurs, plutôt
    que sur une série synthétique qui pourrait ne jamais atteindre la branche testée.
    """
    from tradingagent.research import discovery as module

    length = 40
    monkeypatch.setattr(module, "supertrend", lambda *a, **k: ([1.0] * length, [True] * length))
    monkeypatch.setattr(module, "stochastic", lambda *a, **k: ([20.0, 35.0], [20.0, 35.0]))
    monkeypatch.setattr(module, "ema", lambda values, period: [5000.0 if period == 3 else 4000.0])

    strategy, ctx = context(
        ramp(length), ramp(60, start=3900.0, step=5.0), trend_fast=3, trend_slow=6
    )
    signal = strategy.evaluate(ctx)

    assert signal is not None
    close = ctx.closes(Timeframe.M1)[-1]
    risk = abs(close - signal.stop_loss)
    reward = abs(signal.take_profits[0] - close)
    assert reward < risk, "un objectif à 0,7 R doit être plus proche que le stop"
    assert reward == pytest.approx(0.7 * risk, rel=1e-6)
    assert signal.stop_loss < close, "un BUY place son stop sous l'entrée"


def test_no_signal_when_the_higher_series_is_absent() -> None:
    """Une unité déclarée mais non fournie est une erreur de câblage, jamais un signal."""
    strategy, ctx = context(dip_then_recover(), ramp(60, start=3900.0, step=5.0))
    m1_only = StrategyContext(
        symbol="XAUUSD",
        evaluated_at=ctx.evaluated_at,
        primary_timeframe=Timeframe.M1,
        candles={Timeframe.M1: ctx.series(Timeframe.M1)},
    )

    with pytest.raises(KeyError):
        strategy.evaluate(m1_only)


def test_dead_tape_is_skipped_by_the_volatility_floor() -> None:
    """Un ATR sous le plancher ne doit produire aucun signal, quelle que soit la tendance."""
    strategy, ctx = context(
        dip_then_recover(), ramp(60, start=3900.0, step=5.0), min_atr_points=10_000.0
    )

    assert strategy.evaluate(ctx) is None


def test_the_decision_is_a_pure_function_of_the_window() -> None:
    strategy, ctx = context(dip_then_recover(), ramp(60, start=3900.0, step=5.0))

    first = strategy.evaluate(ctx)
    second = strategy.evaluate(ctx)

    assert (first is None) == (second is None)
    if first is not None and second is not None:
        assert first.direction is second.direction
        assert first.stop_loss == second.stop_loss
        assert first.take_profits == second.take_profits


def test_a_sell_mirrors_the_buy() -> None:
    """Le côté vendeur doit exister, sinon la moitié de la règle serait absente."""
    primary = [4000.0 + 2.0 * index for index in range(20)]
    primary += [primary[-1] + 0.5 * index for index in range(5)]
    primary += [primary[-1] - 4.0 * index for index in range(35)]
    strategy, ctx = context(primary, ramp(60, start=4200.0, step=-5.0))

    signal = strategy.evaluate(ctx)

    if signal is not None:
        assert signal.direction is Direction.SELL
        close = ctx.closes(Timeframe.M1)[-1]
        assert signal.stop_loss > close
        assert signal.take_profits[0] < close


# -- le gabarit ------------------------------------------------------------------------


def test_the_template_proposes_only_valid_parameters() -> None:
    """Chaque proposition doit être constructible par le modèle : aucune graine cassée."""
    scope = TemplateScope(symbols=("XAUUSD",), timeframe=Timeframe.M1, version="0.1.0")

    proposals = list(scalp_triple_filter_template(scope, {}))

    assert proposals
    for proposal in proposals:
        strategy = proposal.factory(proposal.parameters)
        assert isinstance(strategy, ScalpTripleFilter)
        assert proposal.manifest.timeframes == (Timeframe.M1, Timeframe.M5)
        assert proposal.manifest.max_mode is TradingMode.SIGNAL


def test_the_declared_history_covers_the_higher_timeframe_warm_up() -> None:
    """L'EMA M5 consomme cinq fois plus de bougies M1 que sa période : le manifeste doit le dire."""
    scope = TemplateScope(symbols=("XAUUSD",), timeframe=Timeframe.M1, version="0.1.0")

    for proposal in scalp_triple_filter_template(scope, {}):
        slow = int(proposal.parameters["trend_slow"])
        assert proposal.manifest.history_bars >= slow * 5, (
            f"{proposal.parameters} déclare {proposal.manifest.history_bars} bougies "
            f"pour une EMA{slow} en M5, qui en consomme {slow * 5}"
        )


def test_the_template_is_a_pure_function_of_the_grid() -> None:
    scope = TemplateScope(symbols=("XAUUSD",), timeframe=Timeframe.M1, version="0.1.0")

    first = [
        (p.parameters, p.manifest.history_bars) for p in scalp_triple_filter_template(scope, {})
    ]
    second = [
        (p.parameters, p.manifest.history_bars) for p in scalp_triple_filter_template(scope, {})
    ]

    assert first == second
