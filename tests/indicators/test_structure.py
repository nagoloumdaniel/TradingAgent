"""La structure de swing : le recul qui rend la détection utilisable en direct.

Le test le plus important de ce fichier n'est pas « trouve-t-on le sommet ? ». C'est « la
détection regarde-t-elle le futur ? ». Un sommet n'existe qu'avec `strength` barres de recul,
donc la barre courante ne peut jamais en être un — et c'est ce qui autorise à s'en servir au
moment de la décision.
"""

import pytest

from tradingagent.indicators.structure import (
    Structure,
    last_swing_high,
    last_swing_low,
    structure_of,
    swing_highs,
    swing_lows,
)


def repeated(pattern: list[float], times: int) -> list[float]:
    return pattern * times


#: Deux motifs d'une période de 4, dont les extrêmes tombent sur la **même** barre (indice 2),
#: pour que sommets et creux puissent être comparés sur les mêmes indices.
#: Sommets : 10, 14, 20, 14 -> sommet en 2. Creux : 16, 12, 8, 12 -> creux en 2.
ZIGZAG_HIGHS = [10.0, 14.0, 20.0, 14.0]
ZIGZAG_LOWS = [16.0, 12.0, 8.0, 12.0]


def zigzag(
    times: int = 5, low: float = 10.0, high: float = 14.0, peak: float = 20.0
) -> list[float]:
    return repeated([low, high, peak, high], times)


def troughs(times: int = 5) -> list[float]:
    """Les creux correspondant à `zigzag` : l'extrême bas tombe sur la même barre."""
    return repeated(ZIGZAG_LOWS, times)


def test_a_peak_is_confirmed_only_once_the_following_bars_are_closed() -> None:
    """Le cœur du module : la barre courante ne peut pas être un sommet confirmé.

    À la clôture de la barre 2, le sommet est bien en 2 mais il n'est pas encore confirmé :
    il faut la barre 3. C'est ce décalage qui empêche de lire le futur.

    La fonction rend **le dernier sommet confirmé**, pas « cette barre est un sommet » : à la
    barre 5, le dernier connu reste celui de la barre 2, et le suivant (barre 6) ne sera
    publié qu'à la barre 7.
    """
    highs = zigzag(times=2, peak=20.0)

    known = swing_highs(highs, strength=1)

    assert known[2] is None  # le sommet n'est pas encore confirmé
    assert known[3] == 2  # il l'est à la clôture de la barre suivante
    assert known[5] == 2  # aucun nouveau sommet confirmé depuis : le dernier connu demeure
    assert known[6] == 2  # la barre 6 est un sommet, mais pas encore confirmé
    assert known[7] == 6  # confirmé une barre plus tard


def test_the_series_starts_with_no_confirmed_swing() -> None:
    """Avant la première confirmation, il n'y a rien à publier : `None`, jamais un indice
    inventé qui ferait croire à un sommet sans recul."""
    highs = zigzag(times=2)

    known = swing_highs(highs, strength=2)

    assert known[0] is None
    assert known[1] is None


def test_lows_are_detected_where_highs_are_not() -> None:
    """Le creux du motif tombe sur la même barre que le sommet : les deux listes désignent
    les mêmes indices, mais elles ne se confondent pas pour autant."""
    lows = troughs(times=3)

    known = swing_lows(lows, strength=1)

    assert known[1] is None  # pas encore confirmé
    assert known[2] is None  # le creux est à l'indice 2, confirmé seulement ensuite
    assert known[3] == 2
    assert known[7] == 6


def test_a_high_that_is_not_the_highest_is_not_a_swing() -> None:
    """Un sommet doit dépasser **strictement** ses voisins : une suite plate n'en contient
    aucun, sinon chaque barre d'un range serait un sommet."""
    highs = [10.0] * 10

    assert swing_highs(highs, strength=1) == [None] * 10


def test_strictly_greater_excludes_a_plateau() -> None:
    """Deux barres au même plus haut ne font pas un sommet : le comparateur est strict."""
    highs = [10.0, 20.0, 20.0, 10.0]

    assert swing_highs(highs, strength=1) == [None, None, None, None]


def test_the_last_confirmed_low_is_named_directly() -> None:
    """Le trailing sur structure n'a besoin que de ce point : le lire ne doit pas obliger à
    reconstruire toute la liste."""
    assert last_swing_low(troughs(times=3), strength=1) == 8.0


def test_no_confirmed_low_means_no_level() -> None:
    """Une série trop courte rend `None` : il n'y a pas de creux à mettre derrière un stop."""
    assert last_swing_low([10.0, 11.0], strength=2) is None


def test_the_last_confirmed_high_is_named_directly_too() -> None:
    """Le pendant du creux, et il manquait : une position vendeuse suit les **sommets**.

    Sans cette fonction, le trailing sur structure n'aurait fonctionné que dans un sens — ce
    qui est exactement le genre de trou qu'on ne voit qu'en écrivant le code qui s'en sert.
    """
    # 14 puis 13 sont deux sommets confirmés, et c'est **13** qui est le dernier : une série de
    # sommets décroissants est précisément ce qu'un vendeur veut voir.
    assert last_swing_high([10.0, 14.0, 12.0, 13.0, 11.0], strength=1) == 13.0
    assert last_swing_high([10.0, 11.0], strength=2) is None


def test_rising_highs_and_lows_are_an_uptrend() -> None:
    """Sommets et creux montent ensemble : `hh_hl`."""
    highs = repeated(ZIGZAG_HIGHS, 3) + repeated([12.0, 16.0, 24.0, 16.0], 1)
    lows = repeated(ZIGZAG_LOWS, 3) + repeated([18.0, 14.0, 10.0, 14.0], 1)

    assert structure_of(highs, lows, strength=1) is Structure.HIGHER_HIGHS_HIGHER_LOWS


def test_falling_highs_and_lows_are_a_downtrend() -> None:
    highs = repeated([20.0, 16.0, 10.0, 16.0], 3) + repeated([18.0, 14.0, 8.0, 14.0], 1)
    lows = repeated([26.0, 22.0, 18.0, 22.0], 3) + repeated([24.0, 20.0, 16.0, 20.0], 1)

    assert structure_of(highs, lows, strength=1) is Structure.LOWER_HIGHS_LOWER_LOWS


def test_a_contradiction_between_highs_and_lows_is_mixed() -> None:
    """Des sommets qui montent avec des creux qui descendent, c'est un marché qui s'élargit :
    l'appeler une tendance serait une erreur coûteuse."""
    highs = repeated(ZIGZAG_HIGHS, 3) + repeated([12.0, 16.0, 24.0, 16.0], 1)
    lows = repeated(ZIGZAG_LOWS, 3) + repeated([14.0, 10.0, 6.0, 10.0], 1)

    assert structure_of(highs, lows, strength=1) is Structure.MIXED


def test_without_enough_swings_the_structure_is_mixed() -> None:
    """Pas deux sommets à comparer : `MIXED`, et surtout pas une tendance inventée."""
    highs = [10.0, 14.0, 20.0, 14.0]
    lows = [8.0, 12.0, 18.0, 12.0]

    assert structure_of(highs, lows, strength=1) is Structure.MIXED


def test_strength_must_be_at_least_one() -> None:
    with pytest.raises(ValueError, match="strength"):
        swing_highs([1.0, 2.0, 3.0], strength=0)
    with pytest.raises(ValueError, match="strength"):
        structure_of([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], strength=0)


def test_highs_and_lows_must_have_the_same_length() -> None:
    with pytest.raises(ValueError, match="same length"):
        structure_of([1.0, 2.0, 3.0], [1.0, 2.0], strength=1)


def test_the_detection_is_deterministic() -> None:
    highs = zigzag(times=4)
    lows = troughs(times=4)

    assert swing_highs(highs, strength=1) == swing_highs(highs, strength=1)
    assert structure_of(highs, lows, strength=1) is structure_of(highs, lows, strength=1)


@pytest.mark.parametrize("strength", [1, 2, 3])
def test_truncating_the_series_never_changes_what_was_already_published(strength: int) -> None:
    """L'anti-futur, prouvé par troncature : la propriété qui rend le module utilisable.

    Publier une valeur à la barre `j` ne doit dépendre que des barres `0..j`. Si tronquer la
    série après `j` change ce qui était connu à `j`, alors la fonction lisait des barres qui
    n'existaient pas encore au moment de la décision.
    """
    highs = zigzag(times=8)
    lows = troughs(times=8)
    full_highs = swing_highs(highs, strength=strength)
    full_lows = swing_lows(lows, strength=strength)

    for cut in (5, 12, 20, len(highs) - 1):
        assert swing_highs(highs[:cut], strength=strength) == full_highs[:cut]
        assert swing_lows(lows[:cut], strength=strength) == full_lows[:cut]


@pytest.mark.parametrize("strength", [1, 2, 3])
def test_the_current_bar_is_never_a_confirmed_swing(strength: int) -> None:
    """Le corollaire direct : la barre courante ne peut pas être publiée comme extrême."""
    highs = zigzag(times=6)
    lows = troughs(times=6)
    last = len(highs) - 1

    assert swing_highs(highs, strength=strength)[last] != last
    assert swing_lows(lows, strength=strength)[last] != last
