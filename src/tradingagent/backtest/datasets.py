"""Versioned historical datasets (F-025, TASK-060).

A dataset is a JSONL file: one header line carrying identity, provenance and fingerprint,
then one line per raw candle. The fingerprint is a SHA-256 over the candle lines alone, so
any byte changed in the data is detected on load. Files are written once and never
rewritten: raw data is immutable.

Holes are counted with `data.quality.missing_bars` against the market calendar and
reported, never filled. The same checks accept a hand-built synthetic dataset or a real
MT5 download, because both reduce to an ordered tuple of `Candle`.
"""

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from tradingagent.backtest.randomness import DeterministicRandom
from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.market_calendar import MarketCalendar
from tradingagent.data.quality import missing_bars

DATASET_FORMAT = "tradingagent.dataset/1"


class DatasetError(Exception):
    """Base class for dataset defects."""


class DatasetIntegrityError(DatasetError):
    """The stored bytes do not match the fingerprint they claim."""


class DatasetImmutableError(DatasetError):
    """An attempt to overwrite an existing raw dataset."""


@dataclass(frozen=True)
class CandleDataset:
    """One immutable candle series with its identity and provenance."""

    dataset_id: str
    symbol: str
    timeframe: Timeframe
    source: str
    candles: tuple[Candle, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "candles", tuple(self.candles))
        if not self.dataset_id:
            raise DatasetError("dataset_id must not be empty")
        if not self.source:
            raise DatasetError("source must not be empty")
        if not self.candles:
            raise DatasetError(f"dataset {self.dataset_id!r} holds no candle")
        foreign = next(
            (c for c in self.candles if c.timeframe is not self.timeframe),
            None,
        )
        if foreign is not None:
            raise DatasetError(
                f"dataset {self.dataset_id!r} declares {self.timeframe} but holds a "
                f"{foreign.timeframe} candle"
            )
        for earlier, later in zip(self.candles, self.candles[1:], strict=False):
            if earlier.open_time >= later.open_time:
                raise DatasetError(
                    f"dataset {self.dataset_id!r} is not strictly ordered at {later.open_time}"
                )

    @property
    def start(self) -> datetime:
        return self.candles[0].open_time

    @property
    def end(self) -> datetime:
        """Exclusive end: the close time of the last candle."""
        return self.candles[-1].close_time

    @property
    def fingerprint(self) -> str:
        return fingerprint_of(self.candles)

    @property
    def bars(self) -> int:
        return len(self.candles)

    def header(self) -> dict[str, object]:
        return {
            "format": DATASET_FORMAT,
            "dataset_id": self.dataset_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe.value,
            "source": self.source,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "bars": self.bars,
            "fingerprint": self.fingerprint,
        }

    def lines(self) -> list[str]:
        return [json.dumps(self.header(), sort_keys=True)] + [
            candle_line(candle) for candle in self.candles
        ]

    def gaps(self, calendar: MarketCalendar) -> list[datetime]:
        """Bars the calendar marks open between the first and last bar, but the series lacks."""
        return missing_bars(self.candles, self.timeframe, calendar, self.start, self.end)

    def manifest(self, calendar: MarketCalendar) -> "DatasetManifest":
        holes = self.gaps(calendar)
        return DatasetManifest(
            dataset_id=self.dataset_id,
            symbol=self.symbol,
            timeframe=self.timeframe,
            source=self.source,
            start=self.start,
            end=self.end,
            bars=self.bars,
            fingerprint=self.fingerprint,
            gap_count=len(holes),
            first_gap=holes[0] if holes else None,
        )


@dataclass(frozen=True)
class DatasetManifest:
    """The auditable description of a dataset, holes included."""

    dataset_id: str
    symbol: str
    timeframe: Timeframe
    source: str
    start: datetime
    end: datetime
    bars: int
    fingerprint: str
    gap_count: int
    first_gap: datetime | None

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe.value,
            "source": self.source,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "bars": self.bars,
            "fingerprint": self.fingerprint,
            "gap_count": self.gap_count,
            "first_gap": None if self.first_gap is None else self.first_gap.isoformat(),
        }


def candle_line(candle: Candle) -> str:
    """Canonical one-line encoding; Python's float repr round-trips exactly."""
    return json.dumps(
        {
            "t": candle.open_time.isoformat(),
            "o": candle.open,
            "h": candle.high,
            "l": candle.low,
            "c": candle.close,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def fingerprint_of(candles: Sequence[Candle]) -> str:
    digest = hashlib.sha256()
    for candle in candles:
        digest.update(candle_line(candle).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def write_dataset(path: Path, dataset: CandleDataset) -> Path:
    """Freeze a dataset on disk. Refuses to touch an existing file: raw data is immutable."""
    if path.exists():
        raise DatasetImmutableError(
            f"{path} already exists; raw datasets are immutable, choose another path"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(dataset.lines()) + "\n", encoding="utf-8")
    return path


def load_dataset(path: Path) -> CandleDataset:
    """Read a frozen dataset back and verify its fingerprint before trusting a single bar."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise DatasetError(f"{path}: cannot read dataset ({error.strerror})") from None
    lines = [line for line in raw.splitlines() if line.strip()]
    if not lines:
        raise DatasetError(f"{path}: dataset is empty")
    header = _read_header(path, lines[0])
    timeframe = Timeframe(str(header["timeframe"]))
    stored_bars = int(str(header["bars"]))
    stored_fingerprint = str(header["fingerprint"])
    candles = tuple(
        _read_candle(path, number, line, timeframe)
        for number, line in enumerate(lines[1:], start=2)
    )
    if len(candles) != stored_bars:
        raise DatasetIntegrityError(
            f"{path}: header claims {stored_bars} bars, {len(candles)} found"
        )
    actual = fingerprint_of(candles)
    if actual != stored_fingerprint:
        raise DatasetIntegrityError(
            f"{path}: fingerprint mismatch (header {stored_fingerprint[:12]}…, "
            f"content {actual[:12]}…); the raw data was altered"
        )
    return CandleDataset(
        dataset_id=str(header["dataset_id"]),
        symbol=str(header["symbol"]),
        timeframe=Timeframe(str(header["timeframe"])),
        source=str(header["source"]),
        candles=candles,
    )


def _read_header(path: Path, line: str) -> dict[str, object]:
    try:
        header = json.loads(line)
    except json.JSONDecodeError as error:
        raise DatasetError(f"{path}: malformed header line ({error})") from None
    if not isinstance(header, dict) or header.get("format") != DATASET_FORMAT:
        raise DatasetError(f"{path}: not a {DATASET_FORMAT} dataset")
    for key in ("dataset_id", "symbol", "timeframe", "source", "bars", "fingerprint"):
        if key not in header:
            raise DatasetError(f"{path}: header is missing {key!r}")
    return header


def _read_candle(path: Path, number: int, line: str, timeframe: Timeframe) -> Candle:
    try:
        row = json.loads(line)
    except json.JSONDecodeError as error:
        raise DatasetError(f"{path}:{number}: malformed candle line ({error})") from None
    try:
        return Candle(
            timeframe=timeframe,
            open_time=datetime.fromisoformat(str(row["t"])),
            open=float(row["o"]),
            high=float(row["h"]),
            low=float(row["l"]),
            close=float(row["c"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise DatasetError(f"{path}:{number}: unusable candle ({error})") from None


def store_path(directory: Path, dataset: CandleDataset) -> Path:
    return directory / f"{dataset.symbol}-{dataset.timeframe.value}-{dataset.dataset_id}.jsonl"


def save_dataset(directory: Path, dataset: CandleDataset) -> Path:
    return write_dataset(store_path(directory, dataset), dataset)


class DatasetStore:
    """A directory of immutable datasets, addressed by symbol, timeframe and id."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def directory(self) -> Path:
        return self._directory

    def load_all(self) -> dict[str, CandleDataset]:
        """Every dataset in the directory, keyed by symbol.

        Keyed by **symbol**, not by `dataset_id`, and that distinction is the whole point of
        this method. Two datasets fetched on the same day carry the same id — the fetch tool
        derives it from the date — so keying by id silently dropped the second one. On
        2026-10-07 that discarded the entire BTCUSD series while the campaign exited 0 and
        printed "loaded 1 frozen dataset(s)": a two-market run that was quietly a
        one-market run.

        A genuine duplicate is now refused loudly rather than resolved by luck. Two datasets
        for the same symbol would mean the campaign picked one by sort order, and no result
        computed from an arbitrary choice is worth having.
        """
        found: dict[str, CandleDataset] = {}
        sources: dict[str, Path] = {}
        if not self._directory.is_dir():
            return found
        for path in sorted(self._directory.glob("*.jsonl")):
            dataset = load_dataset(path)
            if dataset.symbol in found:
                raise ValueError(
                    f"two datasets for {dataset.symbol}: {sources[dataset.symbol].name} and "
                    f"{path.name}. Give them distinct symbols or move one aside — the "
                    "campaign must never choose between them by file order."
                )
            found[dataset.symbol] = dataset
            sources[dataset.symbol] = path
        return found

    def save(self, dataset: CandleDataset) -> Path:
        return save_dataset(self._directory, dataset)


@dataclass(frozen=True)
class SyntheticRegime:
    """A stretch of `bars` bars with its own drift and volatility."""

    bars: int
    drift: float
    volatility: float


def synthetic_candles(
    timeframe: Timeframe,
    start: datetime,
    regimes: Sequence[SyntheticRegime],
    *,
    seed: int,
    start_price: float = 100.0,
    common_returns: Sequence[float] | None = None,
    beta: float = 1.0,
    decimals: int = 5,
) -> tuple[Candle, ...]:
    """A seeded random walk, optionally driven by a shared factor for correlation studies.

    `common_returns` lets several symbols share one factor: return = beta * factor + noise.
    Every value is round-tripped through the requested number of decimals so two runs of
    the same seed produce byte-identical candles.
    """
    if start.utcoffset() != timedelta(0):
        raise ValueError(f"start must be UTC, got {start!r}")
    if not regimes:
        raise ValueError("at least one regime is required")
    if common_returns is not None and len(common_returns) < sum(r.bars for r in regimes):
        raise ValueError("common_returns is shorter than the requested bar count")

    generator = DeterministicRandom(seed)
    step = timedelta(seconds=timeframe.seconds)
    candles: list[Candle] = []
    price = start_price
    index = 0
    for regime in regimes:
        if regime.volatility < 0:
            raise ValueError("volatility must not be negative")
        for _ in range(regime.bars):
            factor = 0.0 if common_returns is None else float(common_returns[index]) * beta
            move = factor + regime.drift + generator.gauss() * regime.volatility
            opened = price
            closed = _round(opened * _safe_exp(move), decimals)
            wick = abs(generator.gauss()) * regime.volatility * opened
            high = _round(max(opened, closed) + wick, decimals)
            low = _round(min(opened, closed) - wick, decimals)
            opened = _round(opened, decimals)
            candles.append(
                Candle(
                    timeframe=timeframe,
                    open_time=start + step * index,
                    open=opened,
                    high=max(high, opened, closed),
                    low=min(low, opened, closed),
                    close=closed,
                )
            )
            price = closed
            index += 1
    return tuple(candles)


def synthetic_dataset(
    dataset_id: str,
    symbol: str,
    timeframe: Timeframe,
    start: datetime,
    regimes: Sequence[SyntheticRegime],
    *,
    seed: int,
    start_price: float = 100.0,
    common_returns: Sequence[float] | None = None,
    beta: float = 1.0,
    decimals: int = 5,
) -> CandleDataset:
    return CandleDataset(
        dataset_id=dataset_id,
        symbol=symbol,
        timeframe=timeframe,
        source=f"synthetic:splitmix64:{seed}",
        candles=synthetic_candles(
            timeframe,
            start,
            regimes,
            seed=seed,
            start_price=start_price,
            common_returns=common_returns,
            beta=beta,
            decimals=decimals,
        ),
    )


def _round(value: float, decimals: int) -> float:
    return round(value, decimals)


def _safe_exp(value: float) -> float:
    # A research generator must not overflow on an unlucky seed; the cap is ~+/-10%.
    return math.exp(max(-10.0, min(10.0, value)))
