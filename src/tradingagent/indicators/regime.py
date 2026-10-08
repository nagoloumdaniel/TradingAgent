"""Le régime de marché : la question que la stratégie ne sait pas poser aujourd'hui.

« La stratégie gagne en tendance forte et perd en range » est la découverte la plus utile
qu'une analyse puisse faire, et elle est **indétectable** dans le dépôt : rien ne dit si le
marché tend, oscille, casse ou s'endort. Ces fonctions le disent, à partir de trois mesures
qu'on peut toutes expliquer à voix haute.

* **Tendance** : deux moyennes mobiles, et leur pente **normalisée par l'ATR**. Normaliser est
  ce qui rend le seuil comparable entre l'or à 4 000 et le bitcoin à 80 000 : une pente de
  2 points vaut beaucoup sur l'un et rien sur l'autre.
* **Volatilité** : l'ATR du moment rapporté à sa propre moyenne, donc un rapport et non un
  niveau. Un ATR de 3 points ne veut rien dire seul.
* **Structure** : où la clôture se situe par rapport au canal des `channel` barres
  **précédentes**. Les précédentes, pas les actuelles : inclure la barre courante ferait
  disparaître toute cassure, puisque le plus haut courant contient la clôture courante.

Les fonctions rendent `NEUTRAL`, `NORMAL` ou `RANGE` quand la mesure n'est pas définie. Ce
n'est pas une valeur par défaut commode : c'est l'aveu qu'avec trop peu de barres on ne sait
pas, et il vaut mieux que d'affirmer une direction tirée du bruit.
"""

from collections.abc import Sequence
from enum import StrEnum

from tradingagent.indicators.moving_average import ema
from tradingagent.indicators.volatility import atr

#: Sous ce rapport de pente (en ATR par barre), on ne qualifie pas la direction.
DEFAULT_TREND_THRESHOLD = 0.05

#: Bornes du régime de volatilité, exprimées en ATR courant / ATR moyen.
DEFAULT_CALM_RATIO = 0.7
DEFAULT_VOLATILE_RATIO = 1.5


class Trend(StrEnum):
    NEUTRAL = "neutral"
    UP = "up"
    DOWN = "down"


class Volatility(StrEnum):
    CALM = "calm"
    NORMAL = "normal"
    VOLATILE = "volatile"


class MarketStructure(StrEnum):
    RANGE = "range"
    BREAKOUT_UP = "breakout_up"
    BREAKOUT_DOWN = "breakout_down"


def slope_in_atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    slow: int,
    atr_period: int,
) -> float | None:
    """La pente d'une barre de la moyenne lente, exprimée en ATR, ou `None` si indéfinie.

    C'est la seule mesure de tendance du module, et elle est normalisée **ici** : un appelant
    qui compare deux marchés n'a pas à connaître l'ATR de chacun.
    """
    if slow < 2:
        raise ValueError(f"slow must be at least 2, got {slow}")
    values = ema(closes, slow)
    if len(values) < 2 or values[-1] is None or values[-2] is None:
        return None
    volatility = atr(highs, lows, closes, atr_period)[-1]
    if volatility is None or volatility <= 0:
        return None
    return (values[-1] - values[-2]) / volatility


def trend_of(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    fast: int,
    slow: int,
    atr_period: int,
    threshold: float = DEFAULT_TREND_THRESHOLD,
) -> Trend:
    """Direction du marché, ou `NEUTRAL` quand elle n'est pas assez nette pour être nommée.

    Les deux moyennes doivent s'accorder sur le sens **et** la pente normalisée doit dépasser
    le seuil. Un croisement sans pente est un chevauchement, pas une tendance.
    """
    if fast >= slow:
        raise ValueError(f"fast ({fast}) must be shorter than slow ({slow})")
    slope = slope_in_atr(highs, lows, closes, slow=slow, atr_period=atr_period)
    if slope is None:
        return Trend.NEUTRAL
    fast_value = ema(closes, fast)[-1]
    slow_value = ema(closes, slow)[-1]
    if fast_value is None or slow_value is None:
        return Trend.NEUTRAL
    if slope > threshold and fast_value > slow_value:
        return Trend.UP
    if slope < -threshold and fast_value < slow_value:
        return Trend.DOWN
    return Trend.NEUTRAL


def atr_ratio(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    period: int,
) -> float | None:
    """ATR courant / ATR moyen des `period` dernières barres, ou `None` s'il est indéfini.

    Le rapport vaut 1 quand la volatilité est exactement à sa moyenne. C'est un nombre, pas
    un verdict : les seuils qui en font un régime appartiennent à l'appelant.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    values = atr(highs, lows, closes, period)
    current = values[-1]
    if current is None:
        return None
    window = [value for value in values[-period:] if value is not None]
    if not window:
        return None
    average = sum(window) / len(window)
    if average <= 0:
        return None
    return current / average


def volatility_of(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    atr_period: int,
    lookback: int = 100,
    calm_ratio: float = DEFAULT_CALM_RATIO,
    volatile_ratio: float = DEFAULT_VOLATILE_RATIO,
) -> Volatility:
    """Le calme ou l'agitation, en rapport avec la volatilité récente du même marché.

    Un ATR de 3 points ne dit rien seul : trois points sont énormes sur un marché endormi et
    négligeables sur un marché agité. C'est le rapport à sa propre moyenne qui informe.
    """
    if calm_ratio > volatile_ratio:
        raise ValueError(f"calm_ratio ({calm_ratio}) must not exceed volatile_ratio")
    ratio = atr_ratio(highs, lows, closes, period=lookback)
    if ratio is None:
        return Volatility.NORMAL
    if ratio < calm_ratio:
        return Volatility.CALM
    if ratio > volatile_ratio:
        return Volatility.VOLATILE
    return Volatility.NORMAL


def market_structure(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], *, channel: int
) -> MarketStructure:
    """Sortie du canal des `channel` barres précédentes, ou oscillation à l'intérieur.

    La barre courante est exclue du canal **exprès** : l'y inclure rendrait toute cassure
    impossible, le plus haut courant contenant par construction la clôture courante.
    """
    if channel < 1:
        raise ValueError(f"channel must be >= 1, got {channel}")
    if len(closes) < channel + 1:
        return MarketStructure.RANGE
    previous_high = max(highs[-channel - 1 : -1])
    previous_low = min(lows[-channel - 1 : -1])
    close = closes[-1]
    if close > previous_high:
        return MarketStructure.BREAKOUT_UP
    if close < previous_low:
        return MarketStructure.BREAKOUT_DOWN
    return MarketStructure.RANGE


def is_breakout(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], *, channel: int
) -> bool:
    """Vrai quand la clôture sort du canal des `channel` barres précédentes.

    Nommé séparément parce que c'est le seul usage qu'une stratégie en fait : décider si elle
    a le droit d'entrer. Le **sens** de la cassure n'importe pas ici — une cassure haute et
    une cassure basse sont deux sorties de range, et la mesure du 2026-10-09 montre qu'elles
    se comportent de la même façon pour les règles testées (PF 1,652 contre 1,656 sur l'or,
    1,658 contre 1,719 sur le BTC).
    """
    return market_structure(highs, lows, closes, channel=channel) is not MarketStructure.RANGE
