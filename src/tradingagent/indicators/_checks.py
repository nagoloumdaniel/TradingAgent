import math
from collections.abc import Sequence


def require_period(period: int) -> None:
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")


def require_finite(values: Sequence[float]) -> None:
    """Refuse toute valeur non finie, en nommant le premier index fautif.

    Le parcours est fait en C (`all` sur `map`), sans construire de liste : sur une série de
    400 valeurs il coûte ~3 fois moins qu'une boucle Python, et sur les 8 millions d'appels
    minuscules d'un backtest c'est le chemin nominal qui décide du prix.

    Le message d'erreur a besoin de l'index : il n'est donc reconstruit **que** dans le cas
    fautif, par un second parcours. La signature exige une `Sequence`, qui se relit — et si un
    appelant passe malgré tout un itérateur à usage unique, la garde finale refuse au lieu de
    laisser passer une valeur non finie en silence.
    """
    if all(map(math.isfinite, values)):
        return
    for index, value in enumerate(values):
        if not math.isfinite(value):
            raise ValueError(f"value at index {index} is not finite: {value}")
    raise ValueError("a value is not finite, but the input cannot be re-read to name its index")


def wilder(previous: float, value: float, period: int) -> float:
    return (previous * (period - 1) + value) / period
