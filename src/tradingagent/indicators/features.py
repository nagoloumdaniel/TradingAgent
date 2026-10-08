"""La photographie du contexte : quelles conditions régnaient quand une décision a été prise.

C'est la pièce qui manquait pour relier un **contexte** à un **résultat**. Sans elle, une
analyse ne peut corréler que des paramètres à des résultats — « cette période de 14 gagne-t-elle ? »
— et jamais les conditions du marché : « cette règle gagne-t-elle quand le marché est en range
et la volatilité basse ? ». C'est pourtant la découverte la plus utile de toute la spec
d'analyse, et elle était indétectable.

Ce module ne calcule rien lui-même : il **compose** les indicateurs purs déjà testés
(`regime`, `structure`, `session`) et range leurs verdicts sous des clés numériques, parce
qu'un dictionnaire de flottants se sérialise, s'agrège et se compte, tandis qu'une chaîne ne
se compte pas.

Deux règles de lecture, et elles comptent :

1. **une mesure indéfinie n'est pas publiée.** Avec trop peu de barres pour l'ATR, la tendance
   n'est pas inventée : la clé est absente. Un appelant qui lit une clé manquante doit donc
   comprendre « non mesuré », et surtout pas « zéro » ;
2. **les verdicts sont des rangs d'énumération**, dans l'ordre de déclaration. `Session.OFF`
   vaut 0, donc une clé absente se lit comme « hors séance », ce qui est la lecture prudente.
"""

from collections.abc import Sequence
from datetime import datetime

from tradingagent.indicators.regime import (
    DEFAULT_CALM_RATIO,
    DEFAULT_TREND_THRESHOLD,
    DEFAULT_VOLATILE_RATIO,
    MarketStructure,
    Trend,
    Volatility,
    atr_ratio,
    market_structure,
    slope_in_atr,
    trend_of,
)
from tradingagent.indicators.session import Session, session_at
from tradingagent.indicators.structure import Structure, structure_of
from tradingagent.indicators.volatility import atr


def entry_features(
    moments: Sequence[datetime],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    fast: int = 10,
    slow: int = 30,
    atr_period: int = 14,
    lookback: int = 100,
    channel: int = 20,
    swing_strength: int = 2,
    trend_threshold: float = DEFAULT_TREND_THRESHOLD,
) -> dict[str, float]:
    """Le contexte de marché en une fois, sous des clés numériques et stables.

    Toutes les séries doivent être alignées et ordonnées, la dernière barre étant celle de la
    décision. Le dictionnaire rendu peut n'être que partiel pour une série trop courte :
    c'est un résultat honnête, pas une erreur.
    """
    if not (len(moments) == len(highs) == len(lows) == len(closes)):
        raise ValueError("moments, highs, lows and closes must have the same length")
    if not closes:
        return {}
    values: dict[str, float] = {}
    volatility = atr(highs, lows, closes, atr_period)[-1]
    if volatility is not None and volatility > 0:
        values["atr"] = volatility
    ratio = atr_ratio(highs, lows, closes, period=lookback)
    if ratio is not None:
        values["atr_ratio"] = ratio
        if ratio < DEFAULT_CALM_RATIO:
            values["volatility"] = float(list(Volatility).index(Volatility.CALM))
        elif ratio > DEFAULT_VOLATILE_RATIO:
            values["volatility"] = float(list(Volatility).index(Volatility.VOLATILE))
        else:
            values["volatility"] = float(list(Volatility).index(Volatility.NORMAL))
    values["trend"] = float(
        list(Trend).index(
            trend_of(
                highs,
                lows,
                closes,
                fast=fast,
                slow=slow,
                atr_period=atr_period,
                threshold=trend_threshold,
            )
        )
    )
    slope = slope_in_atr(highs, lows, closes, slow=slow, atr_period=atr_period)
    if slope is not None:
        values["slope_atr"] = slope
    values["structure"] = float(
        list(MarketStructure).index(market_structure(highs, lows, closes, channel=channel))
    )
    values["swing"] = float(
        list(Structure).index(structure_of(highs, lows, strength=swing_strength))
    )
    values["session"] = float(list(Session).index(session_at(moments[-1])))
    return values
