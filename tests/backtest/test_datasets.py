"""TASK-060: versioned datasets, immutability, integrity and hole census.

The gap expected value is hand-computed: a strictly regular M15 series with one bar
removed has exactly one hole, at the removed bar's open time, and `missing_bars` only
counts a bar when the calendar says the market was open.

The volume section at the end pins the JSONL contract: `v` is written only when a candle
carries one, so the eight datasets frozen before volume existed keep the exact bytes their
stored fingerprint was computed over.
"""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tradingagent.backtest.datasets import (
    DATASET_FORMAT,
    CandleDataset,
    DatasetError,
    DatasetImmutableError,
    DatasetIntegrityError,
    DatasetStore,
    SyntheticRegime,
    candle_line,
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
ROOT = Path(__file__).resolve().parents[2]
FROZEN_DATASETS = ROOT / "docs" / "research" / "datasets"


def candle(index: int, close: float, volume: float | None = None) -> Candle:
    return Candle(
        timeframe=M15,
        open_time=START + STEP * index,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=volume,
    )


def bars(count: int) -> tuple[Candle, ...]:
    return tuple(candle(index, 100.0 + index) for index in range(count))


def measured_bars(count: int) -> tuple[Candle, ...]:
    """The same series, every bar carrying a volume of 1000 ticks and more."""
    return tuple(candle(index, 100.0 + index, volume=1000.0 + index) for index in range(count))


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


# ----------------------------------------------------------------------------------- volume
#
# A candle carries a volume or it does not. The line format keeps that distinction in the
# bytes: `v` is written only when there is a volume to write, so a dataset frozen before the
# field existed reloads as `volume=None` and keeps the fingerprint it was frozen with.

#: A candle line exactly as `candle_line` produced it before the volume field existed.
LEGACY_LINE = '{"c":101.0,"h":102.0,"l":100.0,"o":100.0,"t":"2026-10-03T00:00:00+00:00"}'


def test_round_trip_preserves_volume(tmp_path) -> None:
    original = gold_dataset(measured_bars(6))

    loaded = load_dataset(write_dataset(tmp_path / "gold.jsonl", original))

    assert [item.volume for item in loaded.candles] == [1000.0 + index for index in range(6)]
    assert loaded.candles == original.candles
    assert loaded.fingerprint == original.fingerprint
    assert '"v":1000.0' in (tmp_path / "gold.jsonl").read_text(encoding="utf-8")


def test_round_trip_keeps_an_absent_volume_absent(tmp_path) -> None:
    """Nothing to record, nothing written: a legacy series must not gain a `v` key."""
    path = write_dataset(tmp_path / "gold.jsonl", gold_dataset())

    loaded = load_dataset(path)

    assert [item.volume for item in loaded.candles] == [None] * 6
    assert '"v"' not in path.read_text(encoding="utf-8")


def test_a_line_without_a_volume_key_still_loads(tmp_path) -> None:
    """The eight datasets frozen before 2026-10-08 carry no `v` key: their bytes must verify.

    The line below is written by hand, as the old writer produced it, and the fingerprint is
    the SHA-256 of those very bytes rather than of a re-encoding. If the reader started
    inventing a `v` key, the stored fingerprint would stop matching and all eight would be
    refused on load. This is the test that protects them.
    """
    fingerprint = hashlib.sha256((LEGACY_LINE + "\n").encode("ascii")).hexdigest()
    header = {
        "format": DATASET_FORMAT,
        "dataset_id": "gold-m15-frozen-before-volume",
        "symbol": GOLD,
        "timeframe": M15.value,
        "source": "mt5",
        "start": START.isoformat(),
        "end": (START + STEP).isoformat(),
        "bars": 1,
        "fingerprint": fingerprint,
    }
    path = tmp_path / "legacy.jsonl"
    path.write_text(
        json.dumps(header, sort_keys=True) + "\n" + LEGACY_LINE + "\n", encoding="utf-8"
    )

    dataset = load_dataset(path)

    assert dataset.candles[0].volume is None
    assert candle_line(dataset.candles[0]) == LEGACY_LINE
    assert dataset.fingerprint == fingerprint


def test_a_zero_volume_survives_the_round_trip(tmp_path) -> None:
    """Zero is a measurement -- a bar with no trade -- and must not collapse into `None`."""
    series = (candle(0, 100.0, volume=0.0), candle(1, 101.0), candle(2, 102.0, volume=7.0))

    loaded = load_dataset(write_dataset(tmp_path / "gold.jsonl", gold_dataset(series)))

    assert [item.volume for item in loaded.candles] == [0.0, None, 7.0]
    assert loaded.candles[0].volume is not None
    assert loaded.candles[1].volume is None


def test_two_series_differing_only_by_volume_have_different_fingerprints() -> None:
    """The volume rides in the fingerprint: identical prices are not an identical series."""
    without = gold_dataset()
    measured = gold_dataset(measured_bars(6))

    assert [item.close for item in without.candles] == [item.close for item in measured.candles]
    assert without.fingerprint != measured.fingerprint
    assert without.header()["fingerprint"] != measured.header()["fingerprint"]


def test_rewriting_a_dataset_produces_the_same_bytes(tmp_path) -> None:
    dataset = gold_dataset(measured_bars(6))

    first = write_dataset(tmp_path / "first.jsonl", dataset)
    second = write_dataset(tmp_path / "second.jsonl", dataset)

    assert first.read_bytes() == second.read_bytes()


def test_synthetic_volume_is_reproducible_per_seed() -> None:
    regimes = (SyntheticRegime(bars=50, drift=0.0, volatility=0.01),)

    first = synthetic_candles(M15, START, regimes, seed=7)
    second = synthetic_candles(M15, START, regimes, seed=7)
    other = synthetic_candles(M15, START, regimes, seed=8)

    volumes = [item.volume for item in first]
    assert volumes == [item.volume for item in second]
    assert volumes != [item.volume for item in other]
    assert all(volume is not None and volume >= 0 for volume in volumes)


def test_the_volume_draws_do_not_move_the_synthetic_prices() -> None:
    """These four candles were generated before volume existed; they must not move.

    Volume comes from its own seeded stream, so no `gauss()` call of the price walk is
    displaced. Drawing it from the price stream instead would leave every bar's volume
    plausible and silently rewrite every synthetic series and fingerprint in the repository.
    """
    regimes = (SyntheticRegime(bars=4, drift=0.001, volatility=0.01),)

    candles = synthetic_candles(M15, START, regimes, seed=7, start_price=100.0)

    assert [(item.open, item.high, item.low, item.close) for item in candles] == [
        (100.0, 101.19908, 99.89534, 101.09442),
        (101.09442, 102.17617, 98.24475, 99.3265),
        (99.3265, 100.5199, 98.23637, 99.42977),
        (99.42977, 100.41504, 98.01859, 99.00386),
    ]


def test_the_frozen_datasets_still_load_without_volume() -> None:
    """The eight real datasets frozen before volume existed, read back from disk.

    `load_dataset` recomputes the SHA-256 of every line and refuses a mismatch, so a green
    run here is the proof that the new field did not touch a byte they already carry.
    """
    paths = sorted(FROZEN_DATASETS.rglob("*.jsonl"))
    assert len(paths) >= 8, f"only {len(paths)} frozen dataset(s) under {FROZEN_DATASETS}"

    for path in paths:
        dataset = load_dataset(path)
        assert dataset.bars > 0, path.name
        assert all(item.volume is None for item in dataset.candles), path.name
