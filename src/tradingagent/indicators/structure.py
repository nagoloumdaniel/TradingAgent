"""La structure de swing : où sont les sommets et les creux, et ce qu'ils racontent.

C'est la brique qui manque pour deux usages précis.

1. **Ta stratégie BTC place son stop derrière les *higher lows***, et un trailing sur
   structure ne peut pas s'écrire sans savoir où sont les creux. Le dépôt ne savait le faire
   qu'à distance fixe (`harness.trailing_stop_atr`).
2. **Ta spec d'analyse demande HH/HL/LH/LL, break of structure et range high/low.** Ces
   fonctions les fournissent, causalement.

**Le point qui décide de tout : un swing n'existe qu'avec du recul.** Un sommet « à
`strength` barres » se confirme seulement quand `strength` barres **suivantes** sont closes.
La barre la plus récente ne peut donc jamais être un swing confirmé, et c'est une bonne
nouvelle : c'est exactement ce qui rend la détection utilisable en direct sans lire le futur.

Une fonction qui renverrait un swing sur la dernière barre serait en train d'utiliser des
données qui n'existent pas encore au moment de la décision. Ce module ne le fait pas, et un
test le vérifie.
"""

from collections.abc import Sequence
from enum import StrEnum


class Structure(StrEnum):
    """Ce que disent les deux derniers sommets et les deux derniers creux."""

    #: Plus haut plus haut **et** plus bas plus haut : la tendance monte.
    HIGHER_HIGHS_HIGHER_LOWS = "hh_hl"
    #: Plus haut plus bas **et** plus bas plus bas : la tendance descend.
    LOWER_HIGHS_LOWER_LOWS = "lh_ll"
    #: Les sommets et les creux ne racontent pas la même chose, ou il n'y en a pas assez.
    MIXED = "mixed"


def swing_highs(highs: Sequence[float], *, strength: int = 2) -> list[int | None]:
    """Pour chaque barre, l'indice du dernier sommet confirmé, ou `None`.

    Un sommet à l'indice `i` est confirmé quand `highs[i]` dépasse strictement les
    `strength` barres qui le précèdent **et** les `strength` qui le suivent. L'indice `i`
    n'est donc publiable qu'à partir de `i + strength`, et `swing_highs(...)[j]` répond à la
    question « à la clôture de `j`, quel sommet était connu ? ».

    La comparaison est **stricte** : une suite plate ne contient aucun sommet, sinon chaque
    barre d'un range en serait un.
    """
    return _confirmed_extremes(highs, strength, higher=True)


def swing_lows(lows: Sequence[float], *, strength: int = 2) -> list[int | None]:
    """Le pendant bas de `swing_highs` : le dernier creux confirmé connu à chaque barre."""
    return _confirmed_extremes(lows, strength, higher=False)


def _confirmed_extremes(
    values: Sequence[float], strength: int, *, higher: bool
) -> list[int | None]:
    if strength < 1:
        raise ValueError(f"strength must be >= 1, got {strength}")
    count = len(values)
    confirmed: list[int | None] = [None] * count
    latest: int | None = None
    for index in range(count):
        # Un extrême n'est publiable qu'avec `strength` barres de recul : c'est ce décalage
        # qui interdit de lire le futur.
        candidate = index - strength
        if candidate >= strength and _is_extreme(values, candidate, strength, higher=higher):
            latest = candidate
        confirmed[index] = latest
    return confirmed


def _is_extreme(values: Sequence[float], index: int, strength: int, *, higher: bool) -> bool:
    value = values[index]
    neighbours = (*values[index - strength : index], *values[index + 1 : index + strength + 1])
    return (
        all(value > other for other in neighbours)
        if higher
        else all(value < other for other in neighbours)
    )


def last_swing_low(lows: Sequence[float], *, strength: int = 2) -> float | None:
    """Le prix du dernier creux confirmé, ou `None` si la série n'en porte aucun."""
    confirmed = swing_lows(lows, strength=strength)
    index = confirmed[-1] if confirmed else None
    return None if index is None else lows[index]


def structure_of(highs: Sequence[float], lows: Sequence[float], *, strength: int = 2) -> Structure:
    """Ce que disent les **deux derniers** sommets et les deux derniers creux confirmés.

    `MIXED` n'est pas un échec : c'est le cas normal d'un marché qui ne va nulle part de
    façon lisible, et le confondre avec une tendance serait le défaut le plus coûteux de ce
    module. Il couvre aussi le manque d'historique, où il n'y a rien à comparer.
    """
    if len(highs) != len(lows):
        raise ValueError(
            f"highs and lows must have the same length, got {len(highs)} and {len(lows)}"
        )
    top = _last_two(swing_highs(highs, strength=strength))
    bottom = _last_two(swing_lows(lows, strength=strength))
    if top is None or bottom is None:
        return Structure.MIXED
    first_high, last_high = top
    first_low, last_low = bottom
    if highs[last_high] > highs[first_high] and lows[last_low] > lows[first_low]:
        return Structure.HIGHER_HIGHS_HIGHER_LOWS
    if highs[last_high] < highs[first_high] and lows[last_low] < lows[first_low]:
        return Structure.LOWER_HIGHS_LOWER_LOWS
    return Structure.MIXED


def _last_two(confirmed: Sequence[int | None]) -> tuple[int, int] | None:
    """Les deux derniers indices distincts de la série, ou `None` s'il y en a moins de deux."""
    seen: list[int] = []
    for index in reversed(confirmed):
        if index is None:
            break
        if not seen or seen[-1] != index:
            seen.append(index)
        if len(seen) == 2:
            return seen[1], seen[0]
    return None
