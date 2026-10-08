"""Tests du Stochastic, l'indicateur de timing de la stratégie M1.

Le Stochastic ne définit pas une tendance : il dit où le prix se situe dans son propre
intervalle récent. C'est cette lecture-là dont un scalping a besoin pour entrer, et c'est
pourquoi ses bords (0 et 100) sont testés explicitement plutôt que supposés.
"""

import math

import pytest

from tradingagent.indicators.stochastic import stochastic, stochastic_k


def recovery() -> tuple[list[float], list[float], list[float]]:
    """Huit bougies dont le %K brut est connu à la main.

    L'intervalle reste [0, 10] : seule la position de la clôture change, ce qui donne des
    %K bruts de 20, 50, 80, 100, 40 puis 20 -- une sortie de survente, le cas que la
    stratégie doit savoir lire.
    """
    highs = [10.0] * 8
    lows = [0.0] * 8
    closes = [2.0, 5.0, 8.0, 10.0, 4.0, 2.0, 6.0, 9.0]
    return highs, lows, closes


def test_k_is_the_close_position_in_the_recent_range() -> None:
    """À l'index 2 : plus bas 0,0, plus haut 10,0, clôture 8,0 -> %K = 80,0."""
    highs, lows, closes = recovery()

    values = stochastic_k(highs, lows, closes, 3)

    assert values[0] is None
    assert values[1] is None
    assert values[2] == pytest.approx(80.0)
    assert values[:3] == [None, None, pytest.approx(80.0)]


def test_a_close_at_the_top_of_the_range_is_one_hundred() -> None:
    """Le bord haut doit valoir exactement 100 : c'est le seuil de surachat de la stratégie."""
    highs = [10.0, 10.0, 10.0, 10.0]
    lows = [8.0, 8.0, 8.0, 8.0]
    closes = [9.0, 9.0, 9.0, 10.0]

    values = stochastic_k(highs, lows, closes, 3)

    assert values[-1] == pytest.approx(100.0)


def test_a_close_at_the_bottom_of_the_range_is_zero() -> None:
    """Le bord bas doit valoir exactement 0 : c'est le seuil de survente de la stratégie."""
    highs = [10.0, 10.0, 10.0, 10.0]
    lows = [8.0, 8.0, 8.0, 8.0]
    closes = [9.0, 9.0, 9.0, 8.0]

    values = stochastic_k(highs, lows, closes, 3)

    assert values[-1] == pytest.approx(0.0)


def test_a_flat_range_has_no_defined_reading() -> None:
    """Un intervalle plat n'a pas de position : renvoyer 50 fabriquerait une lecture neutre."""
    highs = [10.0, 10.0, 10.0]
    lows = [10.0, 10.0, 10.0]
    closes = [10.0, 10.0, 10.0]

    values = stochastic_k(highs, lows, closes, 3)

    assert values[-1] is None


def test_smoothing_averages_the_raw_readings_and_shifts_them() -> None:
    """%K lissé = moyenne des 3 derniers %K bruts ; %D = moyenne des 3 derniers %K lissés.

    Brut  : index 2 -> 80, 3 -> 100, 4 -> 40, 5 -> 20
    Lissé : la première fenêtre pleine est [2:5], donc la première valeur est à l'index 4
    %D    : la première fenêtre pleine de lissés est [4:7], donc à l'index 6
    """
    highs, lows, closes = recovery()

    k, d = stochastic(highs, lows, closes, 3, 3, 3)

    assert k[3] is None  # la fenêtre [1:4] contient encore un brut indéfini
    first = (80.0 + 100.0 + 40.0) / 3
    second = (100.0 + 40.0 + 20.0) / 3
    third = (40.0 + 20.0 + 60.0) / 3
    assert k[4] == pytest.approx(first)
    assert k[5] == pytest.approx(second)
    assert k[6] == pytest.approx(third)
    assert d[5] is None  # deux lissés seulement : le %D n'existe pas encore
    # La moyenne des trois lissés est écrite ici en clair : la lire depuis `k` rendrait le
    # test dépendant de l'implémentation qu'il est justement censé juger.
    assert d[6] == pytest.approx((first + second + third) / 3)


def test_smoothing_of_one_is_the_identity() -> None:
    """Un lissage de 1 ne doit rien décaler : c'est le garde-fou de l'implémentation."""
    highs, lows, closes = recovery()

    k, d = stochastic(highs, lows, closes, 3, 1, 1)

    assert k == pytest.approx(stochastic_k(highs, lows, closes, 3))
    assert d == pytest.approx(k)


def test_inputs_are_validated() -> None:
    highs, lows, closes = recovery()

    with pytest.raises(ValueError, match="same length"):
        stochastic_k(highs, lows[:-1], closes, 3)
    with pytest.raises(ValueError, match="period"):
        stochastic_k(highs, lows, closes, 0)
    with pytest.raises(ValueError, match="not finite"):
        stochastic_k(highs, lows, [float("nan"), *closes[1:]], 3)
    with pytest.raises(ValueError, match="not finite"):
        stochastic_k(highs, [math.inf, *lows[1:]], closes, 3)


def test_two_calls_on_the_same_input_give_the_same_output() -> None:
    """Fonction pure : aucune lecture d'horloge, aucun état conservé entre deux appels."""
    highs, lows, closes = recovery()

    assert stochastic(highs, lows, closes, 3, 3, 3) == stochastic(highs, lows, closes, 3, 3, 3)
