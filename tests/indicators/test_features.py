"""La photographie du contexte : ce qu'elle contient, et surtout ce qu'elle refuse d'inventer.

Le point délicat n'est pas de remplir un dictionnaire. C'est qu'une mesure **indéfinie** soit
absente plutôt que zéro : « je ne sais pas » et « la valeur est nulle » se ressemblent une fois
en base, et les confondre fausserait toute corrélation faite ensuite.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tradingagent.indicators.features import entry_features
from tradingagent.indicators.regime import MarketStructure, Trend
from tradingagent.indicators.session import Session
from tradingagent.indicators.structure import Structure

START = datetime(2026, 9, 25, 7, 0, tzinfo=UTC)  # 07:00 UTC : ouverture de Londres


def moments(count: int) -> list[datetime]:
    """Une barre par minute pendant `count` minutes, en restant dans la même séance."""
    return [START + timedelta(minutes=index) for index in range(count)]


def series(closes: list[float], width: float = 1.0) -> tuple[list, list, list]:
    return (
        [price + width for price in closes],
        [price - width for price in closes],
        closes,
    )


def rising(count: int, start: float = 100.0, step: float = 1.0) -> list[float]:
    return [start + step * index for index in range(count)]


def test_a_full_history_publishes_every_context_key() -> None:
    closes = rising(150)
    highs, lows, _ = series(closes)

    values = entry_features(moments(150), highs, lows, closes)

    assert set(values) == {
        "atr",
        "atr_ratio",
        "volatility",
        "trend",
        "slope_atr",
        "structure",
        "swing",
        "session",
    }


def test_a_rising_market_is_published_as_an_uptrend() -> None:
    closes = rising(150)
    highs, lows, _ = series(closes)

    values = entry_features(moments(150), highs, lows, closes)

    assert values["trend"] == float(list(Trend).index(Trend.UP))
    assert values["slope_atr"] > 0


def test_the_session_is_the_one_the_timestamp_falls_into() -> None:
    """07:00 UTC ouvre Londres : la série de 150 minutes y reste entièrement."""
    closes = rising(150)
    highs, lows, _ = series(closes)

    values = entry_features(moments(150), highs, lows, closes)

    assert values["session"] == float(list(Session).index(Session.LONDON))


def test_the_session_follows_the_timestamp_and_not_the_series_position() -> None:
    """Mêmes prix, décalés de 5 heures : la séance change, pas les mesures de prix.

    07:00-09:29 UTC est en séance de Londres ; 12:00-14:29 UTC est dans l'overlap
    Londres/New York, la fenêtre la plus liquide.
    """
    closes = rising(150)
    highs, lows, _ = series(closes)
    morning = entry_features(moments(150), highs, lows, closes)
    afternoon = entry_features(
        [moment + timedelta(hours=5) for moment in moments(150)], highs, lows, closes
    )

    assert morning["session"] == float(list(Session).index(Session.LONDON))
    assert afternoon["session"] == float(list(Session).index(Session.OVERLAP))
    assert morning["atr"] == afternoon["atr"]


def test_an_undefined_measure_is_absent_rather_than_zero() -> None:
    """Sans assez de barres, l'ATR n'existe pas : la clé doit manquer, pas valoir zéro.

    C'est la règle qui protège toute corrélation faite ensuite : un zéro serait lu comme une
    volatilité nulle, c'est-à-dire un marché mort, alors qu'on ne sait simplement rien.
    """
    closes = [100.0, 100.0, 100.0]
    highs, lows, _ = series(closes)

    values = entry_features(moments(3), highs, lows, closes, atr_period=14, lookback=100)

    assert "atr" not in values
    assert "atr_ratio" not in values
    assert "volatility" not in values
    assert "slope_atr" not in values
    # Ce qui reste mesurable reste publié : la séance et la structure ne demandent pas d'ATR.
    assert values["session"] == float(list(Session).index(Session.LONDON))
    assert "trend" in values


def test_an_undefined_trend_is_neutral_and_not_absent() -> None:
    """La tendance a toujours un verdict, y compris `NEUTRAL` : elle est publiée."""
    closes = [100.0] * 30
    highs, lows, _ = series(closes)

    values = entry_features(moments(30), highs, lows, closes)

    assert values["trend"] == float(list(Trend).index(Trend.NEUTRAL))


def test_the_structure_reflects_a_breakout() -> None:
    """Une clôture au-dessus du canal est publiée comme `BREAKOUT_UP`."""
    closes = [*([100.0] * 40), 120.0]
    highs, lows, _ = series(closes)

    values = entry_features(moments(len(closes)), highs, lows, closes, channel=20)

    assert values["structure"] == float(list(MarketStructure).index(MarketStructure.BREAKOUT_UP))


def test_the_swing_verdict_is_one_of_the_three_declared_ones() -> None:
    closes = rising(150)
    highs, lows, _ = series(closes)

    values = entry_features(moments(150), highs, lows, closes)

    assert values["swing"] in {float(list(Structure).index(item)) for item in Structure}


def test_an_empty_series_returns_nothing_instead_of_failing() -> None:
    assert entry_features([], [], [], []) == {}


def test_misaligned_series_are_refused() -> None:
    """Des séries de longueurs différentes produiraient une photographie d'un autre instant."""
    with pytest.raises(ValueError, match="same length"):
        entry_features(moments(10), [1.0] * 9, [1.0] * 10, [1.0] * 10)


def test_the_features_are_deterministic() -> None:
    closes = rising(150)
    highs, lows, _ = series(closes)

    first = entry_features(moments(150), highs, lows, closes)
    second = entry_features(moments(150), highs, lows, closes)

    assert first == second
