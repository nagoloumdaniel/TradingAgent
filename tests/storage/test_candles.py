from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, func, select

from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.models import CandleRow

START = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)
STEP = timedelta(minutes=15)
INGESTED = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def m15(first: int, count: int, start: datetime = START) -> list[Candle]:
    return [
        Candle(Timeframe.M15, start + STEP * i, 100.0 + i, 101.5 + i, 99.25 + i, 100.75 + i)
        for i in range(first, first + count)
    ]


def stored(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(CandleRow)).scalar_one()


def with_volume(first: int, count: int, volume: float | None) -> list[Candle]:
    return [
        Candle(
            Timeframe.M15,
            START + STEP * i,
            100.0 + i,
            101.5 + i,
            99.25 + i,
            100.75 + i,
            volume=volume,
        )
        for i in range(first, first + count)
    ]


# -- volume : la base doit le conserver, sinon un VWAP de production est aveugle ----------


def test_a_stored_volume_comes_back_identical(engine: Engine) -> None:
    """Sans colonne, l'agent collecte le volume puis le perd en écrivant : le VWAP de
    production ne verrait alors aucun volume, alors que le flux en porte un."""
    store = CandleStore(engine)
    store.save("XAUUSD", with_volume(0, 3, 42.0), INGESTED)

    restored = store.latest("XAUUSD", Timeframe.M15, 3)

    assert [candle.volume for candle in restored] == [42.0, 42.0, 42.0]


def test_a_candle_without_volume_stays_without_volume(engine: Engine) -> None:
    """Les barres écrites avant la colonne n'ont pas de volume : elles doivent rester `None`,
    et jamais devenir 0 — « non enregistré » n'est pas « aucun échange »."""
    store = CandleStore(engine)
    store.save("XAUUSD", m15(0, 2), INGESTED)

    restored = store.latest("XAUUSD", Timeframe.M15, 2)

    assert [candle.volume for candle in restored] == [None, None]


def test_a_zero_volume_survives_as_zero(engine: Engine) -> None:
    """Une barre sans échange est une mesure à zéro, pas une donnée manquante."""
    store = CandleStore(engine)
    store.save("XAUUSD", with_volume(0, 2, 0.0), INGESTED)

    restored = store.latest("XAUUSD", Timeframe.M15, 2)

    assert [candle.volume for candle in restored] == [0.0, 0.0]


def test_the_volume_survives_the_between_window(engine: Engine) -> None:
    """Les deux chemins de lecture doivent porter le volume, pas seulement `latest`."""
    store = CandleStore(engine)
    store.save("XAUUSD", with_volume(0, 4, 7.5), INGESTED)

    restored = store.between("XAUUSD", Timeframe.M15, START, START + STEP * 2)

    assert [candle.volume for candle in restored] == [7.5, 7.5]


def test_the_first_stored_bar_wins_even_for_the_volume(engine: Engine) -> None:
    """Une barre close ne change plus : le volume stocké ne se réécrit pas non plus."""
    store = CandleStore(engine)
    store.save("XAUUSD", with_volume(0, 1, 10.0), INGESTED)
    store.save("XAUUSD", with_volume(0, 1, 99.0), INGESTED)

    restored = store.latest("XAUUSD", Timeframe.M15, 1)

    assert restored[0].volume == 10.0


def test_saved_candles_come_back_identical_and_in_order(engine: Engine) -> None:
    store = CandleStore(engine)
    candles = m15(0, 10)
    assert store.save("XAUUSD", list(reversed(candles)), INGESTED) == 10
    assert store.latest("XAUUSD", Timeframe.M15, 10) == candles


def test_saving_the_same_candles_twice_creates_no_duplicate(engine: Engine) -> None:
    store = CandleStore(engine)
    store.save("XAUUSD", m15(0, 10), INGESTED)
    assert store.save("XAUUSD", m15(0, 10), INGESTED) == 0
    assert stored(engine) == 10


def test_an_overlapping_catch_up_inserts_only_the_new_candles(engine: Engine) -> None:
    store = CandleStore(engine)
    store.save("XAUUSD", m15(0, 10), INGESTED)
    assert store.save("XAUUSD", m15(6, 10), INGESTED) == 6
    assert store.latest("XAUUSD", Timeframe.M15, 100) == m15(0, 16)


def test_first_stored_version_of_a_candle_wins(engine: Engine) -> None:
    store = CandleStore(engine)
    store.save("XAUUSD", m15(0, 1), INGESTED)
    revised = Candle(Timeframe.M15, START, 1.0, 2.0, 0.5, 1.5)
    store.save("XAUUSD", [revised], INGESTED)
    assert store.latest("XAUUSD", Timeframe.M15, 1) == m15(0, 1)


def test_series_are_kept_apart_by_symbol_and_timeframe(engine: Engine) -> None:
    store = CandleStore(engine)
    hourly = [Candle(Timeframe.H1, START, 1.0, 1.0, 1.0, 1.0)]
    assert store.save("XAUUSD", m15(0, 4), INGESTED) == 4
    assert store.save("BTCUSD", m15(0, 4), INGESTED) == 4
    assert store.save("XAUUSD", hourly, INGESTED) == 1
    assert store.latest("XAUUSD", Timeframe.H1, 10) == hourly
    assert len(store.latest("BTCUSD", Timeframe.M15, 10)) == 4


def test_latest_returns_the_most_recent_candles_oldest_first(engine: Engine) -> None:
    store = CandleStore(engine)
    store.save("XAUUSD", m15(0, 20), INGESTED)
    assert store.latest("XAUUSD", Timeframe.M15, 3) == m15(17, 3)


def test_latest_can_stop_at_the_candles_closed_by_a_moment(engine: Engine) -> None:
    store = CandleStore(engine)
    store.save("XAUUSD", m15(0, 20), INGESTED)
    closed_by = START + STEP * 10  # the candle opened at 9 closes exactly then
    assert store.latest("XAUUSD", Timeframe.M15, 3, closed_by=closed_by) == m15(7, 3)


def test_between_is_half_open(engine: Engine) -> None:
    store = CandleStore(engine)
    store.save("XAUUSD", m15(0, 20), INGESTED)
    window = store.between("XAUUSD", Timeframe.M15, START + STEP * 5, START + STEP * 8)
    assert window == m15(5, 3)


def test_last_open_time(engine: Engine) -> None:
    store = CandleStore(engine)
    assert store.last_open_time("XAUUSD", Timeframe.M15) is None
    store.save("XAUUSD", m15(0, 7), INGESTED)
    assert store.last_open_time("XAUUSD", Timeframe.M15) == START + STEP * 6


def test_saving_nothing_is_a_no_op(engine: Engine) -> None:
    assert CandleStore(engine).save("XAUUSD", [], INGESTED) == 0


def test_source_and_ingestion_time_are_recorded(engine: Engine) -> None:
    CandleStore(engine, source="mt5").save("XAUUSD", m15(0, 1), INGESTED)
    with engine.connect() as connection:
        row = connection.execute(select(CandleRow.source, CandleRow.ingested_at)).one()
    assert row == ("mt5", INGESTED)
