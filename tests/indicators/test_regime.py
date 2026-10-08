"""Le régime de marché : chaque mesure sur une série dont la réponse est connue d'avance.

Ces tests portent sur des séries construites à la main, pas sur des données réelles : une
classification de régime ne se juge pas sur un exemple, elle se juge sur les cas limites —
une pente juste au seuil, un canal touché sans être cassé, une volatilité qui change de
rapport.
"""

import pytest

from tradingagent.indicators.regime import (
    MarketStructure,
    Trend,
    Volatility,
    atr_ratio,
    market_structure,
    slope_in_atr,
    trend_of,
    volatility_of,
)


def rising(count: int, start: float = 100.0, step: float = 1.0) -> list[float]:
    return [start + step * index for index in range(count)]


def falling(count: int, start: float = 100.0, step: float = 1.0) -> list[float]:
    return [start - step * index for index in range(count)]


def flat(count: int, price: float = 100.0) -> list[float]:
    return [price] * count


def quiet(closes: list[float], width: float = 1.0) -> tuple[list[float], list[float]]:
    """La même série de clôtures, encadrée par des bougies plus ou moins larges."""
    return [price + width for price in closes], [price - width for price in closes]


# -- tendance ------------------------------------------------------------------------------


def test_a_steady_rise_is_an_uptrend() -> None:
    closes = rising(60)
    highs, lows = quiet(closes)

    assert trend_of(highs, lows, closes, fast=10, slow=30, atr_period=14) is Trend.UP


def test_a_steady_fall_is_a_downtrend() -> None:
    closes = falling(60)
    highs, lows = quiet(closes)

    assert trend_of(highs, lows, closes, fast=10, slow=30, atr_period=14) is Trend.DOWN


def test_a_flat_market_is_neutral() -> None:
    """Sans pente, il n'y a pas de direction : nommer une tendance serait l'inventer."""
    closes = flat(60)
    highs, lows = quiet(closes)

    assert trend_of(highs, lows, closes, fast=10, slow=30, atr_period=14) is Trend.NEUTRAL


def test_the_same_slope_is_not_a_trend_when_the_market_is_wild() -> None:
    """La preuve de la normalisation : même pente en points, verdicts opposés.

    Les deux séries montent identiquement (1 point par barre). La première a des bougies de
    2 points de large, la seconde de 200. Rapportée à l'ATR, la même pente vaut alors
    beaucoup dans un cas et presque rien dans l'autre : c'est ce qui rend le seuil
    comparable entre deux marchés qui ne cotent pas dans la même unité.
    """
    closes = rising(60, step=1.0)
    tight_high, tight_low = quiet(closes, width=1.0)
    wide_high, wide_low = quiet(closes, width=100.0)

    tight = slope_in_atr(tight_high, tight_low, closes, slow=30, atr_period=14)
    wide = slope_in_atr(wide_high, wide_low, closes, slow=30, atr_period=14)

    assert tight is not None and wide is not None
    assert tight > 0.05 > wide
    # Donc le même seuil donne deux verdicts différents sur la même pente en points.
    assert trend_of(tight_high, tight_low, closes, fast=10, slow=30, atr_period=14) is Trend.UP
    assert trend_of(wide_high, wide_low, closes, fast=10, slow=30, atr_period=14) is Trend.NEUTRAL


def test_a_crossing_without_a_slope_is_not_a_trend() -> None:
    """Deux moyennes qui se croisent sans pente normalisée ne font pas une direction."""
    closes = flat(60)
    highs, lows = quiet(closes)

    assert (
        trend_of(highs, lows, closes, fast=10, slow=30, atr_period=14, threshold=0.0)
        is Trend.NEUTRAL
    )


def test_a_series_too_short_to_compute_is_neutral() -> None:
    """Pas assez de barres pour les moyennes : aucune direction ne peut être affirmée."""
    closes = rising(5)
    highs, lows = quiet(closes)

    assert trend_of(highs, lows, closes, fast=10, slow=30, atr_period=14) is Trend.NEUTRAL


def test_the_fast_average_must_be_shorter_than_the_slow_one() -> None:
    closes = rising(60)
    highs, lows = quiet(closes)

    with pytest.raises(ValueError, match="fast"):
        trend_of(highs, lows, closes, fast=30, slow=10, atr_period=14)


def test_a_slope_of_one_bar_needs_two_bars() -> None:
    closes = rising(60)
    highs, lows = quiet(closes)

    with pytest.raises(ValueError, match="slow"):
        slope_in_atr(highs, lows, closes, slow=1, atr_period=14)


# -- volatilité ----------------------------------------------------------------------------


def contraction(wide_bars: int = 60, narrow_bars: int = 120) -> tuple[list, list, list]:
    """Des barres larges puis des barres étroites : la volatilité se contracte.

    Le nombre de barres étroites est **mesuré**, pas choisi : le lissage de Wilder garde une
    mémoire longue (facteur 13/14 par barre), donc 40 barres étroites ne font tomber le
    rapport qu'à 0,98 — pas de quoi parler de calme. Il en faut 120 pour atteindre 0,62.
    """
    count = wide_bars + narrow_bars
    closes = [100.0] * count
    highs = [100.0 + (10.0 if index < wide_bars else 0.01) for index in range(count)]
    lows = [100.0 - (10.0 if index < wide_bars else 0.01) for index in range(count)]
    return highs, lows, closes


def test_a_volatility_contraction_is_read_as_calm() -> None:
    """Un ATR courant nettement sous sa propre moyenne : le marché s'endort."""
    highs, lows, closes = contraction()

    verdict = volatility_of(highs, lows, closes, atr_period=14, lookback=95)

    assert verdict is Volatility.CALM


def test_a_volatility_expansion_is_read_as_volatile() -> None:
    """Une série étroite puis des barres très larges : l'ATR courant dépasse sa moyenne."""
    closes = flat(120, 100.0)
    highs = [price + 0.01 for price in closes[:-20]] + [110.0] * 20
    lows = [price - 0.01 for price in closes[:-20]] + [90.0] * 20

    verdict = volatility_of(highs, lows, closes, atr_period=14, lookback=100)

    assert verdict is Volatility.VOLATILE


def test_the_two_bounds_are_read_from_the_parameters() -> None:
    """Les mêmes données classées « calme » par les bornes par défaut et « normal » par des
    bornes larges : c'est la preuve que les seuils sont lus et non figés dans le code."""
    highs, lows, closes = contraction()

    with_defaults = volatility_of(highs, lows, closes, atr_period=14, lookback=95)
    with_wide_bounds = volatility_of(
        highs, lows, closes, atr_period=14, lookback=95, calm_ratio=0.0, volatile_ratio=1e9
    )

    assert with_defaults is Volatility.CALM
    assert with_wide_bounds is Volatility.NORMAL


def test_volatility_falls_back_to_normal_without_history() -> None:
    """Sans assez de barres pour comparer, on ne qualifie pas : « normal » est l'absence de
    verdict, et elle vaut mieux qu'un verdict tiré d'une moyenne inexistante."""
    closes = flat(10)
    highs, lows = quiet(closes)

    assert volatility_of(highs, lows, closes, atr_period=14, lookback=100) is Volatility.NORMAL


def test_the_atr_ratio_is_undefined_when_the_average_is_zero() -> None:
    """Un marché parfaitement immobile n'a pas d'ATR : le rapport n'existe pas."""
    closes = flat(120)
    highs, lows = quiet(closes, width=0.0)

    assert atr_ratio(highs, lows, closes, period=100) is None


def test_an_incoherent_bound_pair_is_refused() -> None:
    closes = flat(120)
    highs, lows = quiet(closes)

    with pytest.raises(ValueError, match="calm_ratio"):
        volatility_of(highs, lows, closes, atr_period=14, calm_ratio=2.0, volatile_ratio=1.0)


# -- structure -----------------------------------------------------------------------------


def test_a_close_above_the_previous_high_is_a_breakout_up() -> None:
    """Le canal exclut la barre courante : sinon aucune cassure ne serait possible, le plus
    haut courant contenant par construction la clôture courante."""
    closes = [*flat(30), 110.0]
    highs, lows = quiet(closes)

    assert market_structure(highs, lows, closes, channel=20) is MarketStructure.BREAKOUT_UP


def test_a_close_below_the_previous_low_is_a_breakout_down() -> None:
    closes = [*flat(30), 90.0]
    highs, lows = quiet(closes)

    assert market_structure(highs, lows, closes, channel=20) is MarketStructure.BREAKOUT_DOWN


def test_a_close_inside_the_channel_is_a_range() -> None:
    closes = flat(30)
    highs, lows = quiet(closes)

    assert market_structure(highs, lows, closes, channel=20) is MarketStructure.RANGE


def test_touching_the_boundary_without_breaking_it_is_still_a_range() -> None:
    """Le contact exact n'est pas une cassure : il faut la dépasser."""
    closes = flat(30)
    highs, lows = quiet(closes)
    closes[-1] = highs[-2]  # la clôture touche le plus haut précédent, sans le dépasser

    assert market_structure(highs, lows, closes, channel=20) is MarketStructure.RANGE


def test_a_channel_longer_than_the_series_is_a_range() -> None:
    closes = flat(10)
    highs, lows = quiet(closes)

    assert market_structure(highs, lows, closes, channel=50) is MarketStructure.RANGE


def test_a_channel_of_one_bar_still_compares_the_previous_bar() -> None:
    closes = [*flat(10), 200.0]
    highs, lows = quiet(closes)

    assert market_structure(highs, lows, closes, channel=1) is MarketStructure.BREAKOUT_UP


def test_a_channel_of_zero_is_refused() -> None:
    closes = flat(10)
    highs, lows = quiet(closes)

    with pytest.raises(ValueError, match="channel"):
        market_structure(highs, lows, closes, channel=0)


def test_the_structure_is_deterministic() -> None:
    closes = [*flat(30), 90.0]
    highs, lows = quiet(closes)

    first = market_structure(highs, lows, closes, channel=20)
    second = market_structure(highs, lows, closes, channel=20)

    assert first is second
