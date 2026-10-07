"""TASK-060: versioned datasets, immutability, integrity and hole census.

The gap expected value is hand-computed: a strictly regular M15 series with one bar
removed has exactly one hole, at the removed bar's open time, and `missing_bars` only
counts a bar when the calendar says the market was open.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tradingagent.backtest.datasets import (
    DATASET_FORMAT,
    CandleDataset,
    DatasetError,
    DatasetImmutableError,
    DatasetIntegrityError,
    DatasetStore,
    SyntheticRegime,
    load_dataset,
    synthetic_candles,
    synthetic_dataset,
    write_dataset,
)
from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.market_calendar import MarketCalendar

START = datetime(2026, 10, 3, tzinfo=UTC)
GOLD = "frxXAUUSD"
M15 = Timeframe.M15
STEP = timedelta(seconds=M15.seconds)
ALWAYS_OPEN = frozenset((day, quarter) for day in range(7) for quarter in range(96))


def candle(index: int, close: float) -> Candle:
    return Candle(
        timeframe=M15,
        open_time=START + STEP * index,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
    )


def bars(count: int) -> tuple[Candle, ...]:
    return tuple(candle(index, 100.0 + index) for index in range(count))


def gold_dataset(candles: tuple[Candle, ...] | None = None) -> CandleDataset:
    return CandleDataset(
        dataset_id="gold-m15-demo",
        symbol=GOLD,
        timeframe=M15,
        source="test",
        candles=candles if candles is not None else bars(6),
    )


def calendar() -> MarketCalendar:
    return MarketCalendar(symbol=GOLD, open_slots=ALWAYS_OPEN, uncertain_slots=frozenset())


def test_header_carries_identity_period_source_and_fingerprint(tmp_path) -> None:
    path = write_dataset(tmp_path / "gold.jsonl", gold_dataset())
    dataset = load_dataset(path)
    assert dataset.dataset_id == "gold-m15-demo"
    assert dataset.symbol == GOLD
    assert dataset.timeframe is M15
    assert dataset.source == "test"
    assert dataset.start == START
    assert dataset.end == START + STEP * 6
    assert dataset.bars == 6
    assert len(dataset.fingerprint) == 64


def test_round_trip_preserves_every_bar(tmp_path) -> None:
    original = gold_dataset()
    loaded = load_dataset(write_dataset(tmp_path / "gold.jsonl", original))
    assert loaded.candles == original.candles
    assert loaded.fingerprint == original.fingerprint


def test_raw_data_is_never_overwritten(tmp_path) -> None:
    path = write_dataset(tmp_path / "gold.jsonl", gold_dataset())
    before = path.read_bytes()
    with pytest.raises(DatasetImmutableError):
        write_dataset(path, gold_dataset(bars(3)))
    assert path.read_bytes() == before


def test_a_single_altered_bar_is_detected(tmp_path) -> None:
    path = write_dataset(tmp_path / "gold.jsonl", gold_dataset())
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[3] = lines[3].replace('"o":102.0', '"o":102.5')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(DatasetIntegrityError):
        load_dataset(path)


def test_unsorted_candles_are_refused() -> None:
    shuffled = list(bars(4))
    shuffled[1], shuffled[2] = shuffled[2], shuffled[1]
    with pytest.raises(DatasetError, match="ordered"):
        gold_dataset(tuple(shuffled))


def test_empty_or_foreign_candles_are_refused() -> None:
    with pytest.raises(DatasetError, match="no candle"):
        gold_dataset(())
    foreign = Candle(
        timeframe=Timeframe.H1,
        open_time=START,
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
    )
    with pytest.raises(DatasetError, match="H1"):
        gold_dataset((foreign,))


def test_gap_census_lists_exactly_the_missing_open_bar() -> None:
    present = [candle(index, 100.0 + index) for index in range(8) if index != 3]
    dataset = gold_dataset(tuple(present))
    holes = dataset.gaps(calendar())
    assert holes == [START + STEP * 3]
    manifest = dataset.manifest(calendar())
    assert manifest.gap_count == 1
    assert manifest.first_gap == START + STEP * 3
    assert manifest.fingerprint == dataset.fingerprint


def test_gaps_are_reported_not_filled() -> None:
    present = [candle(index, 100.0 + index) for index in range(8) if index != 3]
    dataset = gold_dataset(tuple(present))
    assert dataset.bars == 7
    assert [c.open_time for c in dataset.candles] == [
        START + STEP * index for index in range(8) if index != 3
    ]


def test_store_loads_every_immutable_dataset(tmp_path) -> None:
    store = DatasetStore(tmp_path)
    store.save(gold_dataset())
    found = store.load_all()
    assert set(found) == {gold_dataset().symbol}
    assert found[gold_dataset().symbol].fingerprint == gold_dataset().fingerprint


def test_two_markets_fetched_the_same_day_are_both_loaded(tmp_path) -> None:
    """The defect of 2026-10-07: one market silently vanished from a two-market run.

    Both files carried the same `dataset_id` — the fetch tool derived it from the date — and
    the store keyed its result by that id, so the second overwrote the first. The campaign
    exited 0 and printed "loaded 1 frozen dataset(s)".
    """
    store = DatasetStore(tmp_path)
    store.save(gold_dataset())
    store.save(
        replace(
            gold_dataset(),
            dataset_id="mt5-2026-10-07",  # same day, same id, different market
            symbol="BTCUSD",
        )
    )

    found = store.load_all()

    assert set(found) == {GOLD, "BTCUSD"}


def test_two_datasets_for_one_symbol_are_refused_not_arbitrated(tmp_path) -> None:
    """Choosing between them by file order would make every result an accident."""
    store = DatasetStore(tmp_path)
    store.save(gold_dataset())
    store.save(replace(gold_dataset(), dataset_id="a-second-fetch"))

    with pytest.raises(ValueError) as caught:
        store.load_all()

    assert "two datasets for" in str(caught.value)
    assert "file order" in str(caught.value)


def test_synthetic_data_is_reproducible_per_seed() -> None:
    regimes = (SyntheticRegime(bars=50, drift=0.0, volatility=0.01),)
    first = synthetic_candles(M15, START, regimes, seed=7)
    second = synthetic_candles(M15, START, regimes, seed=7)
    other = synthetic_candles(M15, START, regimes, seed=8)
    assert first == second
    assert first != other
    assert len(first) == 50
    assert all(c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high for c in first)


def test_synthetic_common_factor_drives_correlation() -> None:
    from tradingagent.backtest.randomness import DeterministicRandom

    regimes = (SyntheticRegime(bars=200, drift=0.0, volatility=0.001),)
    stream = DeterministicRandom(3)
    factor = [stream.gauss() for _ in range(200)]
    gold = synthetic_dataset("gold", GOLD, M15, START, regimes, seed=1, common_returns=factor)
    btc = synthetic_dataset("btc", "cryBTCUSD", M15, START, regimes, seed=2, common_returns=factor)
    assert gold.fingerprint != btc.fingerprint
    assert gold.header()["format"] == DATASET_FORMAT
