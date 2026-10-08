"""Le câblage multi-timeframe du harnais de découverte.

Une règle qui décide sur M1 et lit un filtre sur M5 a besoin des deux séries. Ce fichier
vérifie la seule fonction qui les assemble, y compris la propriété qui compte le plus :
la série grossière est agrégée depuis la série **entière**, jamais depuis une tranche, sinon
la même bougie M5 existerait deux fois sous deux formes différentes.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.discovery import _series

START = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)


def bar(index: int, timeframe: Timeframe) -> Candle:
    step = timedelta(seconds=timeframe.seconds)
    price = 4000.0 + index
    return Candle(
        timeframe=timeframe,
        open_time=START + step * index,
        open=price,
        high=price + 0.5,
        low=price - 0.5,
        close=price,
    )


def test_the_declared_higher_timeframe_is_aggregated_from_the_primary() -> None:
    m1 = tuple(bar(index, Timeframe.M1) for index in range(15))

    series = _series({Timeframe.M1: m1}, Timeframe.M1, (Timeframe.M1, Timeframe.M5))

    assert set(series) == {Timeframe.M1, Timeframe.M5}
    assert len(series[Timeframe.M5]) == 3
    assert series[Timeframe.M5][0].open == m1[0].open
    assert series[Timeframe.M5][0].close == m1[4].close


def test_the_primary_series_is_never_rebuilt() -> None:
    """La série primaire doit être rendue telle quelle : la reconstruire changerait les prix."""
    m1 = tuple(bar(index, Timeframe.M1) for index in range(15))

    series = _series({Timeframe.M1: m1}, Timeframe.M1, (Timeframe.M1,))

    assert series[Timeframe.M1] is m1


def test_a_missing_primary_series_is_an_error_not_an_empty_mapping() -> None:
    """Sans série primaire il n'y a rien à mesurer : lever plutôt que rendre du vide."""
    with pytest.raises(ValueError, match="primary timeframe"):
        _series({}, Timeframe.M1, (Timeframe.M1,))


def test_a_declared_timeframe_equal_to_the_primary_is_not_duplicated() -> None:
    m1 = tuple(bar(index, Timeframe.M1) for index in range(5))

    series = _series({Timeframe.M1: m1}, Timeframe.M1, (Timeframe.M1, Timeframe.M1))

    assert set(series) == {Timeframe.M1}
