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
from tradingagent.indicators.session import Session
from tradingagent.strategies.base import StrategyContext
from tradingagent.strategies.library.vwap_pullback import VwapPullback, VwapPullbackParameters

START = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)  # 00:00 UTC : l'ancre du VWAP
STEP = timedelta(minutes=15)


def parameters(**overrides: object) -> VwapPullbackParameters:
    # `object` et non `float` : les filtres ajoutent des paramètres qui ne sont pas des nombres
    # — les séances autorisées forment un tuple, `trend_filter` un booléen.
    base: dict[str, object] = {
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


def rising_pullback(*, length: int | None = None) -> list[float]:
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
    return closes if length is None else closes[:length]


def falling_pullback(*, length: int | None = None) -> list[float]:
    """Le miroir exact : tendance baissière, retour par le dessous, reprise à la baisse."""
    closes = [100.0]
    for _ in range(60):
        closes.append(closes[-1] - 0.04)
    for _ in range(10):
        closes.append(closes[-1] + 0.10)
    for _ in range(2):
        closes.append(closes[-1] - 0.04)
    return closes if length is None else closes[:length]


def context(
    closes: list[float],
    *,
    half_range: float = 0.6,
    half_ranges: list[float] | None = None,
    volumes: list[float] | None = None,
) -> StrategyContext:
    ranges = half_ranges if half_ranges is not None else [half_range] * len(closes)
    measured = volumes if volumes is not None else [100.0] * len(closes)
    candles = tuple(
        Candle(
            timeframe=Timeframe.M15,
            open_time=START + STEP * index,
            open=price,
            high=price + ranges[index],
            low=price - ranges[index],
            close=price,
            volume=measured[index],
        )
        for index, price in enumerate(closes)
    )
    return StrategyContext(
        symbol="BTCUSD",
        evaluated_at=candles[-1].close_time,
        primary_timeframe=Timeframe.M15,
        candles={Timeframe.M15: candles},
    )


def strategy(**overrides: object) -> VwapPullback:
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


# --------------------------------------------------------------------------------------
# Les filtres de la spec opérateur — tous DÉSACTIVÉS par défaut
# --------------------------------------------------------------------------------------


def test_every_filter_is_off_by_default() -> None:
    """Un filtre qui change le comportement sans qu'on l'ait demandé n'est pas un filtre.

    C'est la condition qui rend la comparaison honnête : la règle mesurée reste celle qui
    tourne tant que personne n'a prouvé qu'un filtre apporte quelque chose.
    """
    default = parameters()

    assert default.allowed_sessions == ()
    assert default.trend_filter is False
    assert default.volume_ratio_min is None


def test_the_session_filter_refuses_a_bar_outside_the_allowed_windows() -> None:
    """L'heure de la bougie qui déclenche est la seule horloge que la règle ait le droit de lire.

    La dernière barre du scénario ouvre à 18:00 UTC : elle appartient à la séance de New York.
    En n'autorisant que l'overlap Londres/New York, la règle doit se taire — c'est la
    vérification que le filtre regarde bien la bougie et non l'horloge de la machine.
    """
    filtered = strategy(allowed_sessions=(Session.OVERLAP,))

    assert filtered.evaluate(context(rising_pullback())) is None


def test_the_session_filter_lets_the_declared_session_through() -> None:
    """Le pendant du test précédent : la même barre passe dès que sa séance est autorisée."""
    filtered = strategy(allowed_sessions=(Session.NEW_YORK,))

    candidate = filtered.evaluate(context(rising_pullback()))

    assert candidate is not None
    assert candidate.direction is Direction.BUY


def test_the_session_filter_refuses_an_empty_but_nonzero_tail() -> None:
    """Un signal hors des fenêtres autorisées disparaît, quel que soit le sens.

    Sans ce test, on ne saurait pas si le silence du filtre vient du filtre ou d'une géométrie
    que le changement de longueur a cassée : la version non filtrée sert de témoin.
    """
    closes = rising_pullback(length=73)

    assert strategy().evaluate(context(closes)) is not None
    assert strategy(allowed_sessions=(Session.TOKYO,)).evaluate(context(closes)) is None


def test_the_trend_filter_refuses_a_market_that_does_not_trend() -> None:
    """Le filtre de tendance lit la pente sur **une** barre, à la différence du momentum.

    Les deux ne mesurent donc pas la même chose : `slope_window` répond « la tendance existe
    depuis dix barres », `trend_of` répond « elle pousse encore maintenant ». Un marché plat
    n'est ni haussier ni baissier, et la règle se tait au lieu de parier sur le bruit.
    """
    filtered = strategy(trend_filter=True)

    assert filtered.evaluate(context([100.0] * 80)) is None


def test_the_trend_filter_refuses_a_downtrend_for_a_buy() -> None:
    """Le momentum de la règle et la tendance du filtre peuvent diverger.

    La série monte puis replie jusqu'à **toucher** le VWAP : la pente sur dix barres reste
    positive (+0,0109 mesurée) alors que celle d'une barre est déjà retournée (-0,0051). C'est
    exactement le désaccord que le filtre doit trancher, et il tranche en refusant. Le témoin
    non filtré est là pour prouver que le silence vient du filtre et non d'une géométrie cassée
    par la fixture.
    """
    closes = rising_pullback()

    assert strategy().evaluate(context(closes)) is not None
    assert strategy(trend_filter=True).evaluate(context(closes)) is None


def test_the_trend_filter_lets_a_real_uptrend_through() -> None:
    """Le pendant positif existe, et il a fallu le chercher sur 105 géométries.

    C'est le résultat le plus utile de cette paire de tests. Sur la fixture nominale — un repli
    qui vient **toucher** le VWAP — la pente d'une barre vaut **-0,0022** : elle est négative
    partout dans le balayage, `trend_of` répond `NEUTRAL`, et avec son seuil par défaut de 0,05
    le filtre refuse **100 %** des signaux. Il faut une reprise après le contact et un seuil
    quasi nul (1e-9, la plus petite valeur que le modèle accepte) pour que les deux mesures
    s'accordent : c'est cette géométrie-ci, et le test la fige.

    Ce test prouve le **mécanisme** — le filtre laisse passer ce qu'il reconnaît comme une
    tendance — pas l'utilité du filtre, qui est mesurée séparément sur le vrai marché.
    """
    closes = [100.0]
    for _ in range(60):
        closes.append(closes[-1] + 0.04)
    for _ in range(6):
        closes.append(closes[-1] - 0.15)
    closes.append(closes[-1] + 0.04)

    filtered = strategy(trend_filter=True, trend_slope_atr=1e-9)
    candidate = filtered.evaluate(context(closes))

    assert candidate is not None
    assert candidate.direction is Direction.BUY
    # L'étiquette est dans le motif, jamais dans `indicators` : `core.signal` n'y accepte que
    # des nombres finis, et un `StrEnum` y ferait lever la construction du signal.
    assert "tendance up" in candidate.reason


def test_the_volume_filter_refuses_a_touch_on_a_dried_up_bar() -> None:
    """Un retour au VWAP sans volume est une barre traversée, pas un niveau défendu.

    Le seuil est relatif à la moyenne des mêmes barres : un volume « faible » n'a de sens que
    comparé à ce que ce marché fait d'habitude, jamais en valeur absolue.
    """
    volumes = [100.0] * len(rising_pullback())
    volumes[-1] = 10.0
    filtered = strategy(volume_ratio_min=1.0)

    assert filtered.evaluate(context(rising_pullback(), volumes=volumes)) is None


def test_the_volume_filter_lets_a_touch_on_an_active_bar_through() -> None:
    """Le pendant : une barre au volume habituel ne doit rien changer."""
    filtered = strategy(volume_ratio_min=1.0)

    candidate = filtered.evaluate(context(rising_pullback()))

    assert candidate is not None
    assert candidate.direction is Direction.BUY


def test_the_volume_filter_measures_the_bar_against_its_predecessors_only() -> None:
    """La barre courante est exclue de sa propre référence, et ce test la sépare du voisin.

    Les vingt barres précédentes valent 100 et la dernière 160 : rapport attendu 1,6. Si la
    barre courante entrait dans sa propre moyenne, le rapport vaudrait 160/103 ≈ 1,55 — un pic
    fort se raboterait lui-même d'autant plus qu'il est fort, ce qui est l'inverse du but.
    """
    closes = rising_pullback()
    volumes = [100.0] * len(closes)
    volumes[-1] = 160.0

    candidate = strategy(volume_ratio_min=1.0).evaluate(context(closes, volumes=volumes))

    assert candidate is not None
    assert candidate.indicators["volume_ratio"] == pytest.approx(1.6, rel=1e-9)


def test_the_parameters_refuse_a_volume_ratio_that_can_never_be_met() -> None:
    """Un seuil nul ou négatif ne filtre rien tout en ayant l'air de filtrer.

    Le refus est explicite plutôt que silencieux : `None` est la façon d'éteindre le filtre.
    """
    with pytest.raises(ValueError, match="volume_ratio_min"):
        parameters(volume_ratio_min=0.0)
