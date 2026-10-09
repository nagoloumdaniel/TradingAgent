"""La stratégie BTCUSD : VWAP comme référence, momentum comme permis, pullback comme entrée.

**La logique, telle que l'opérateur l'a spécifiée.** Le VWAP ancré à 00:00 UTC est le prix
moyen réellement payé par le marché sur la journée : au-dessus, les acheteurs du jour sont
gagnants ; en dessous, les vendeurs le sont. La stratégie ne prend pas position contre ce
niveau, elle **attend qu'on y revienne** — c'est le pullback, et c'est ce qui évite d'acheter
au sommet d'une bougie d'expansion.

**Une tension que ces tests ont mis au jour, et qui a dicté les paramètres par défaut.** Sur un
repli au VWAP, les moyennes convergent : avec des EMA courtes (8/21), la rapide **passe sous**
la lente, et toute condition de tendance haussière devient insatisfiable en même temps que le
contact. Il a fallu mesurer pour le voir — quatre géométries seulement, sur 216 essayées,
satisfont « EMA rapide au-dessus de la lente » et « la mèche touche le VWAP ». D'où des EMA
**20/50** et une tolérance de **0,4 ATR** : ce ne sont pas des choix esthétiques, ce sont les
valeurs sous lesquelles la règle est cohérente.

**La pente se mesure sur 10 barres, pas sur une.** Sur une barre, un repli donne toujours une
pente négative — exiger une pente positive sur une barre interdirait la stratégie entière. Le
momentum est une propriété de la **tendance**, pas du dernier pas.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tradingagent.core.market import Candle, Direction
from tradingagent.core.timeframe import Timeframe
from tradingagent.strategies.base import StrategyContext
from tradingagent.strategies.library.vwap_pullback import VwapPullback, VwapPullbackParameters

START = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)  # 00:00 UTC : l'ancre du VWAP
STEP = timedelta(minutes=15)


def parameters(**overrides: float) -> VwapPullbackParameters:
    base: dict[str, float] = {
        "ema_fast": 20,
        "ema_slow": 50,
        "vwap_period": 20,
        "atr_period": 14,
        "stop_atr_multiplier": 1.5,
        "first_target_rr": 0.8,
        "final_target_rr": 1.5,
        "pullback_atr": 0.4,
        "entry_zone_atr": 0.1,
        "min_slope_atr": 0.005,
        "slope_window": 10,
    }
    return VwapPullbackParameters.model_validate({**base, **overrides})


def rising_pullback() -> list[float]:
    """Tendance haussière, repli qui vient **toucher** le VWAP, puis deux barres de reprise.

    Géométrie mesurée : distance au VWAP 0,46 pour une tolérance de 0,48, EMA rapide au-dessus
    de la lente, pente sur 10 barres à +0,011 pour un seuil de 0,005, et la mèche basse de la
    dernière barre sous le VWAP alors que la clôture repasse au-dessus.
    """
    closes = [100.0]
    for _ in range(60):
        closes.append(closes[-1] + 0.04)
    for _ in range(10):
        closes.append(closes[-1] - 0.10)
    for _ in range(2):
        closes.append(closes[-1] + 0.04)
    return closes


def falling_pullback() -> list[float]:
    """Le miroir exact : tendance baissière, retour par le dessous, reprise à la baisse."""
    closes = [100.0]
    for _ in range(60):
        closes.append(closes[-1] - 0.04)
    for _ in range(10):
        closes.append(closes[-1] + 0.10)
    for _ in range(2):
        closes.append(closes[-1] - 0.04)
    return closes


def context(closes: list[float], *, half_range: float = 0.6) -> StrategyContext:
    candles = tuple(
        Candle(
            timeframe=Timeframe.M15,
            open_time=START + STEP * index,
            open=price,
            high=price + half_range,
            low=price - half_range,
            close=price,
            volume=100.0,
        )
        for index, price in enumerate(closes)
    )
    return StrategyContext(
        symbol="BTCUSD",
        evaluated_at=candles[-1].close_time,
        primary_timeframe=Timeframe.M15,
        candles={Timeframe.M15: candles},
    )


def strategy(**overrides: float) -> VwapPullback:
    return VwapPullback(parameters(**overrides))


# --------------------------------------------------------------------------------------
# La règle, dans les deux sens
# --------------------------------------------------------------------------------------


def test_a_pullback_to_vwap_in_an_uptrend_is_a_buy() -> None:
    """Le scénario nominal : le prix monte, revient toucher le VWAP, et repart."""
    candidate = strategy().evaluate(context(rising_pullback()))

    assert candidate is not None
    assert candidate.direction is Direction.BUY
    assert candidate.stop_loss < candidate.entry_low
    assert candidate.take_profits[0] < candidate.take_profits[1]


def test_a_pullback_up_to_vwap_in_a_downtrend_is_a_sell() -> None:
    """Le miroir : la règle doit fonctionner dans les deux sens, sinon elle n'en a qu'un."""
    candidate = strategy().evaluate(context(falling_pullback()))

    assert candidate is not None
    assert candidate.direction is Direction.SELL
    assert candidate.stop_loss > candidate.entry_high
    assert candidate.take_profits[0] > candidate.take_profits[1]


def test_no_signal_when_the_price_is_far_from_the_vwap() -> None:
    """Une tendance qui s'éloigne du VWAP n'est pas un pullback : rien à faire.

    C'est la condition qui distingue cette stratégie d'un suivi de tendance : sans elle, elle
    achèterait l'extension, ce que la règle refuse explicitement.
    """
    closes = [100.0]
    for _ in range(80):
        closes.append(closes[-1] + 0.5)

    assert strategy().evaluate(context(closes)) is None


def test_no_signal_when_there_is_no_momentum() -> None:
    """Un marché plat n'a ni tendance ni pente : la règle se tait."""
    assert strategy().evaluate(context([100.0] * 80)) is None


def test_no_signal_without_enough_history() -> None:
    """Trop peu de barres pour le VWAP et les EMA : se taire plutôt qu'inventer."""
    assert strategy().evaluate(context([100.0, 101.0, 102.0])) is None


def test_no_signal_without_volume() -> None:
    """Sans volume, un VWAP n'est qu'une moyenne : la règle se tait au lieu de mentir."""

    def without_volume(price: float, index: int) -> Candle:
        return Candle(
            timeframe=Timeframe.M15,
            open_time=START + STEP * index,
            open=price,
            high=price + 0.6,
            low=price - 0.6,
            close=price,
        )

    closes = rising_pullback()
    candles = tuple(without_volume(price, index) for index, price in enumerate(closes))
    ctx = StrategyContext(
        symbol="BTCUSD",
        evaluated_at=candles[-1].close_time,
        primary_timeframe=Timeframe.M15,
        candles={Timeframe.M15: candles},
    )

    assert strategy().evaluate(ctx) is None


# --------------------------------------------------------------------------------------
# Les niveaux
# --------------------------------------------------------------------------------------


def test_the_targets_are_the_declared_multiples_of_the_risk() -> None:
    """TP1 à 0,8 R et TP2 à 1,5 R, mesurés depuis le **prix de référence** publié.

    Ce prix est dans les indicateurs (`reference`) au lieu d'être deviné : le harnais remplit à
    la borne de la zone, donc prendre le milieu de la zone comme référence donnerait un R faux.
    """
    candidate = strategy().evaluate(context(rising_pullback()))

    assert candidate is not None
    reference = candidate.indicators["reference"]
    risk = reference - candidate.stop_loss
    assert candidate.take_profits[0] == pytest.approx(reference + 0.8 * risk, rel=1e-9)
    assert candidate.take_profits[1] == pytest.approx(reference + 1.5 * risk, rel=1e-9)


def test_changing_the_parameters_changes_the_levels() -> None:
    """Aucun niveau n'est codé en dur."""
    candidate = strategy(first_target_rr=1.0, final_target_rr=2.0).evaluate(
        context(rising_pullback())
    )

    assert candidate is not None
    reference = candidate.indicators["reference"]
    risk = reference - candidate.stop_loss
    assert candidate.take_profits[0] == pytest.approx(reference + 1.0 * risk, rel=1e-9)
    assert candidate.take_profits[1] == pytest.approx(reference + 2.0 * risk, rel=1e-9)


def test_the_signal_carries_its_reason_and_its_measurements() -> None:
    """Un signal sans motif ni valeurs n'est pas exploitable pour l'analyse."""
    candidate = strategy().evaluate(context(rising_pullback()))

    assert candidate is not None
    assert "VWAP" in candidate.reason
    assert {"vwap", "reference", "ema_fast", "ema_slow", "atr", "slope_atr"} <= set(
        candidate.indicators
    )


def test_the_momentum_is_measured_over_a_window_and_not_one_bar() -> None:
    """Sur une barre, un repli donne toujours une pente négative.

    Exiger une pente positive sur une seule barre interdirait la stratégie entière : c'est la
    tension que la mise au point a révélée, et ce test la verrouille.
    """
    candidate = strategy().evaluate(context(rising_pullback()))

    assert candidate is not None
    assert candidate.indicators["slope_atr"] > 0


# --------------------------------------------------------------------------------------
# Le contrat des paramètres et la pureté
# --------------------------------------------------------------------------------------


def test_the_parameters_refuse_a_first_target_beyond_the_second() -> None:
    with pytest.raises(ValueError, match="first_target_rr"):
        parameters(first_target_rr=2.0, final_target_rr=1.0)


def test_the_parameters_refuse_a_fast_average_longer_than_the_slow_one() -> None:
    with pytest.raises(ValueError, match="ema_slow"):
        parameters(ema_fast=60, ema_slow=20)


def test_the_parameters_refuse_a_stop_inside_the_entry_zone() -> None:
    with pytest.raises(ValueError, match="entry_zone_atr"):
        parameters(entry_zone_atr=2.0, stop_atr_multiplier=1.5)


def test_the_parameters_refuse_a_pullback_wider_than_the_stop() -> None:
    with pytest.raises(ValueError, match="pullback_atr"):
        parameters(pullback_atr=3.0, stop_atr_multiplier=1.5)


def test_the_same_context_gives_the_same_decision_twice() -> None:
    """Une règle pure : aucune mémoire, aucune horloge, aucun ordre d'appel."""
    rule = strategy()
    context_ = context(rising_pullback())

    first = rule.evaluate(context_)
    second = rule.evaluate(context_)

    assert first == second
