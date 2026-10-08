"""Le volume à travers l'agrégation : une somme complète, ou l'aveu qu'on ne sait pas.

Un VWAP est une moyenne pondérée par le volume. Sommer un volume partiel ne donne pas une
approximation : ça donne un prix qui parle d'une autre série, et qui contredira le même seau
reconstruit depuis un téléchargement complet. Ces tests fixent la seule règle acceptable.
"""

from datetime import UTC, datetime, timedelta

from tradingagent.backtest.aggregate import aggregate
from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe

START = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)


def bar(index: int, volume: float | None, timeframe: Timeframe = Timeframe.M1) -> Candle:
    step = timedelta(seconds=timeframe.seconds)
    price = 4000.0 + index
    return Candle(
        timeframe=timeframe,
        open_time=START + step * index,
        open=price,
        high=price + 0.5,
        low=price - 0.5,
        close=price,
        volume=volume,
    )


def test_a_complete_bucket_sums_its_volumes() -> None:
    """Cinq bougies M1 à 10, 20, 30, 40, 50 ticks : le seau M5 en porte 150."""
    candles = tuple(bar(index, 10.0 * (index + 1)) for index in range(5))

    merged = aggregate(candles, Timeframe.M5)

    assert len(merged) == 1
    assert merged[0].volume == 150.0


def test_a_bucket_missing_one_volume_admits_it_instead_of_summing_a_partial() -> None:
    """Trois volumes connus sur cinq : le seau ne vaut pas 60, il vaut « inconnu »."""
    candles = tuple(bar(index, None if index in (2, 4) else 20.0) for index in range(5))

    merged = aggregate(candles, Timeframe.M5)

    assert merged[0].volume is None


def test_a_series_without_any_volume_stays_without_volume() -> None:
    """Les jeux gelés d'avant le 2026-10-08 n'ont aucun volume : l'agrégat n'en invente pas."""
    candles = tuple(bar(index, None) for index in range(10))

    merged = aggregate(candles, Timeframe.M5)

    assert [item.volume for item in merged] == [None, None]


def test_zero_volume_is_a_measurement_not_a_hole() -> None:
    """Une plage sans échange vaut zéro, et ce zéro doit survivre."""
    candles = tuple(bar(index, 0.0) for index in range(5))

    merged = aggregate(candles, Timeframe.M5)

    assert merged[0].volume == 0.0


def test_each_bucket_sums_its_own_members() -> None:
    candles = tuple(bar(index, float(index)) for index in range(10))

    merged = aggregate(candles, Timeframe.M5)

    assert [item.volume for item in merged] == [0.0 + 1 + 2 + 3 + 4, 5 + 6 + 7 + 8 + 9]


def test_aggregating_twice_sums_the_same_total() -> None:
    """M1 → M15 par M5 doit porter le même volume que M1 → M15 directement."""
    candles = tuple(bar(index, 7.0) for index in range(15))

    direct = aggregate(candles, Timeframe.M15)
    stepwise = aggregate(aggregate(candles, Timeframe.M5), Timeframe.M15)

    assert direct[0].volume == stepwise[0].volume == 105.0


def test_a_partial_trailing_bucket_sums_only_what_it_has() -> None:
    """Un seau final incomplet est conservé, et il somme ses seuls membres présents."""
    candles = tuple(bar(index, 4.0) for index in range(7))

    merged = aggregate(candles, Timeframe.M5)

    assert [item.volume for item in merged] == [20.0, 8.0]


def test_the_price_of_an_aggregate_is_unchanged_by_the_volume_rule() -> None:
    """Le volume ne doit pas toucher aux prix : c'est un ajout, pas une réécriture."""
    candles = tuple(bar(index, None if index == 1 else 3.0) for index in range(5))

    merged = aggregate(candles, Timeframe.M5)[0]

    assert merged.open == candles[0].open
    assert merged.close == candles[-1].close
    assert merged.high == max(item.high for item in candles)
    assert merged.low == min(item.low for item in candles)


def test_the_aggregate_is_deterministic() -> None:
    candles = tuple(bar(index, float(index) * 1.5) for index in range(10))

    assert aggregate(candles, Timeframe.M5) == aggregate(candles, Timeframe.M5)
