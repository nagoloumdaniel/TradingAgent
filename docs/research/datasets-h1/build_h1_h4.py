#!/usr/bin/env python
"""TASK-5 -- freeze H1 and H4 datasets by folding frozen M15 series.

    uv run python docs/research/datasets-h1/build_h1_h4.py self-test
    uv run python docs/research/datasets-h1/build_h1_h4.py build \
        --source docs/research/datasets-long --output docs/research/datasets-h1/long-60000
    uv run python docs/research/datasets-h1/build_h1_h4.py verify \
        --source docs/research/datasets-long --output docs/research/datasets-h1/long-60000

WHY THIS SCRIPT EXISTS
The brief for TASK-5 assumes `src/tradingagent/data/aggregation.py` already folds a series and
refuses an incomplete bar. That module does not exist: `src/tradingagent/` holds no aggregation
and no resampling code at all, and the M1/M5 datasets beside the M15 ones were *fetched* from
MT5 (`scripts/backtest/fetch_mt5_dataset.py --timeframe`), not derived from M15. Nothing in the
repository therefore has a tested opinion on how four quarter-hours fold into an hour, so the
fold lives here, in the research folder, next to the artifacts it produces -- and it is proven
by `self-test` rather than declared. Everything the repository does provide is reused rather
than re-implemented: `load_dataset` (recomputes the SHA-256 and refuses a mismatch), `Candle`
(refuses an incoherent range), `CandleDataset` (refuses a foreign timeframe and an unordered
series), `write_dataset` (refuses to overwrite a raw file), `DatasetStore.load_all` (refuses
two datasets for one symbol), `market_calendar.learn_calendar` (tells a closure from a hole),
and `research.campaign.walk_forward_plan_for` plus `research.protocol.walk_forward` for the
fold count -- the plan is never restated here.

THE GOLDEN RULE
A bar of the target unit is written only when **every** source leg it is made of is present, at
its exact slot on the source grid: four M15 legs for H1, sixteen for H4. Legs are keyed by slot
inside their bucket and a second leg claiming one slot is refused, so "as many legs as the
target needs" and "all the legs the target needs" are the same test, and a bucket holding a
subset -- four contiguous M15 legs inside an H4 window included -- is dropped, never padded and
never filled from a neighbour.

The line format carries `t, o, h, l, c` and no volume (`datasets.candle_line`), so an
incomplete fold would not lie about volume here; it would lie about the high, the low and the
close. The hole is kept visible in three places at once: the frozen series holds a plain time
discontinuity (`gaps`), the census splits every bucket of the target grid into written /
partial / empty, and the sidecar `<dataset>.aggregation.json` lists every dropped bucket with
the leg times it is missing.

WHY "UNFOLDED LEGS" ARE SPLIT THREE WAYS
A leg is missing from the target grid whenever its bucket was dropped, and calling all of them
"lost data" would be a lie of the same kind the golden rule exists to prevent. Each missing leg
is therefore classified on the absent run it belongs to:

* `boundary` -- the slot lies in the first or the last bucket of the grid: the export started
  or stopped inside that bar, nothing is missing from the market;
* `closure`  -- the absent run holding the slot also holds a slot the learned calendar marks
  closed. The rule *widens* every closure by up to one target bar, because a closure usually
  starts and ends inside an hour or a four-hour window; these legs are that widening, plus the
  closures a four-week calendar cannot see (gold's daily break sits at 22:00 UTC in winter and
  21:00 UTC in summer -- 736 of them are the winter window alone);
* `hole`     -- an absent run made only of slots the calendar marks open. That is a hole in the
  data and nothing else, and its exact times are listed in the sidecar.

The three counts sum to the unfolded legs, and the tool refuses to report otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tradingagent.backtest.datasets import (
    CandleDataset,
    DatasetStore,
    load_dataset,
    write_dataset,
)
from tradingagent.config._yaml import read_yaml
from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.market_calendar import MarketCalendar, SlotStatus, learn_calendar
from tradingagent.research.campaign import walk_forward_plan_for
from tradingagent.research.protocol import split_dataset, walk_forward
from tradingagent.strategies.manifest import StrategyManifest

ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = ROOT / "docs" / "research" / "datasets-long"
OUTPUT_DIR = ROOT / "docs" / "research" / "datasets-h1" / "long-60000"
MANIFEST_DIR = ROOT / "config" / "strategies"
SOURCE_TIMEFRAME = Timeframe.M15
TARGETS = (Timeframe.H1, Timeframe.H4)
CALENDAR_WEEKS = 4
CALENDAR_LONG_RATIO = 0.9
#: The unlock word `protocol.split_dataset` wants before it hands out a sealed holdout. It is a
#: deliberate no-op here: this tool only reads the size of the partitions, never their candles.
SEALED_UNLOCK = "task-5-verify"
STAMP = re.compile(r"(\d{4}-\d{2}-\d{2})$")


class RuleError(Exception):
    """The golden rule or an integrity invariant was violated."""


@dataclass(frozen=True)
class Calendars:
    """Two readings of the same market's hours, both learned from the source M15 series.

    `recent` is the schedule in force today -- `learn_calendar` over the last four weeks, the
    calendar the running agent would hold. It cannot know that gold's daily window sat at
    22:00 UTC last winter and sits at 21:00 UTC now, because it only ever saw one of the two.

    `long` reads the whole span with the same function and a stricter ratio: a slot the market
    failed to trade for more than a tenth of its occurrences is not an open slot, it is part of
    the schedule. Together they separate a closure the calendar misread from a hole in the data.
    """

    recent: MarketCalendar
    long: MarketCalendar
    long_weeks: int

    @property
    def symbol(self) -> str:
        return self.recent.symbol

    def status_at(self, moment: datetime) -> SlotStatus:
        """OPEN only when both readings agree the market was trading then."""
        if self.recent.status_at(moment) is not SlotStatus.OPEN:
            return SlotStatus.CLOSED
        return self.long.status_at(moment)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuleError(message)


def grid_floor(moment: datetime, seconds: int) -> datetime:
    """Start of the `seconds`-wide epoch-aligned window holding `moment`, in UTC."""
    if moment.utcoffset() != timedelta(0):
        raise RuleError(f"{moment!r} is not UTC")
    return datetime.fromtimestamp(int(moment.timestamp()) // seconds * seconds, tz=UTC)


def target_ratio(target: Timeframe, source: Timeframe) -> int:
    if source.seconds >= target.seconds:
        raise RuleError(f"{source} is not finer than {target}")
    if target.seconds % source.seconds:
        raise RuleError(f"{target} is not a whole multiple of {source}")
    return target.seconds // source.seconds


@dataclass(frozen=True)
class Census:
    """Every bucket of the target grid over the source span, sorted into three piles.

    `partial` holds `(bucket, slots present)`: the slots are the indices of the legs that did
    arrive, so the leg times the bucket is missing are recoverable to the minute.
    """

    bars: tuple[Candle, ...]
    partial: tuple[tuple[datetime, tuple[int, ...]], ...]
    empty: tuple[datetime, ...]
    source_bars: int
    ratio: int

    @property
    def expected(self) -> int:
        return len(self.bars) + len(self.partial) + len(self.empty)

    @property
    def legs_folded(self) -> int:
        return len(self.bars) * self.ratio

    @property
    def legs_in_partial(self) -> int:
        return sum(len(slots) for _, slots in self.partial)

    @property
    def legs_lost(self) -> int:
        return sum(self.ratio - len(slots) for _, slots in self.partial)

    @property
    def legs_missing_from_empty(self) -> int:
        return len(self.empty) * self.ratio

    @property
    def legs_unfolded(self) -> int:
        return self.legs_lost + self.legs_missing_from_empty


def aggregate(source: CandleDataset, target: Timeframe) -> Census:
    """Fold `source` into `target` bars, writing only the buckets that are complete."""
    ratio = target_ratio(target, source.timeframe)
    source_step = source.timeframe.seconds
    window = timedelta(seconds=target.seconds)
    legs: dict[datetime, dict[int, Candle]] = defaultdict(dict)
    for candle in source.candles:
        if candle.timeframe is not source.timeframe:
            raise RuleError(f"{candle.timeframe} candle inside a {source.timeframe} dataset")
        offset = int(
            (candle.open_time - grid_floor(candle.open_time, target.seconds)).total_seconds()
        )
        if offset % source_step:
            raise RuleError(f"{candle.open_time.isoformat()} is off the {source.timeframe} grid")
        bucket = candle.open_time - timedelta(seconds=offset)
        slot = offset // source_step
        if slot in legs[bucket]:
            raise RuleError(f"two legs claim slot {slot} of {bucket.isoformat()}")
        legs[bucket][slot] = candle

    first = grid_floor(source.start, target.seconds)
    last = grid_floor(source.candles[-1].open_time, target.seconds)
    bars: list[Candle] = []
    partial: list[tuple[datetime, tuple[int, ...]]] = []
    empty: list[datetime] = []
    at = first
    while at <= last:
        present = legs.pop(at, {})
        if not present:
            empty.append(at)
        elif len(present) == ratio:
            ordered = [present[slot] for slot in range(ratio)]
            bars.append(
                Candle(
                    timeframe=target,
                    open_time=at,
                    open=ordered[0].open,
                    high=max(leg.high for leg in ordered),
                    low=min(leg.low for leg in ordered),
                    close=ordered[-1].close,
                )
            )
        else:
            partial.append((at, tuple(sorted(present))))
        at += window
    if legs:
        stray = sum(len(slots) for slots in legs.values())
        raise RuleError(f"{stray} leg(s) fall outside [{first.isoformat()}, {last.isoformat()}]")

    census = Census(tuple(bars), tuple(partial), tuple(empty), source.bars, ratio)
    require(
        census.legs_folded + census.legs_in_partial == source.bars,
        f"leg conservation broken: {census.legs_folded} folded + {census.legs_in_partial} in "
        f"partial buckets != {source.bars} source bars",
    )
    require(
        census.legs_folded + census.legs_in_partial + census.legs_unfolded
        == census.expected * ratio,
        f"{census.expected} buckets hold {census.expected * ratio} slots but "
        f"{census.legs_folded + census.legs_in_partial + census.legs_unfolded} are accounted for",
    )
    return census


# ---------------------------------------------------------------- writing and the sidecar


def source_files(directory: Path) -> list[Path]:
    files = sorted(path for path in directory.glob("*.jsonl"))
    require(bool(files), f"no source dataset under {directory}")
    return files


def stamp_of(dataset: CandleDataset) -> str:
    match = STAMP.search(dataset.dataset_id)
    require(match is not None, f"dataset id {dataset.dataset_id!r} carries no date to stamp")
    return match.group(1)


def target_directory(output: Path, target: Timeframe) -> Path:
    """One directory per target unit: `DatasetStore.load_all` keys by symbol and would refuse
    the H1 and the H4 series of one market sitting side by side."""
    return output / target.value


def dataset_path(directory: Path, symbol: str, target: Timeframe, stamp: str) -> Path:
    return directory / f"{symbol}-{target.value}-from-{SOURCE_TIMEFRAME.value}-{stamp}.jsonl"


def freeze(
    source: CandleDataset, source_path: Path, census: Census, target: Timeframe, directory: Path
) -> CandleDataset:
    stamp = stamp_of(source)
    dataset = CandleDataset(
        dataset_id=f"agg-{SOURCE_TIMEFRAME.value.lower()}-{source.symbol}-{target.value}-{stamp}",
        symbol=source.symbol,
        timeframe=target,
        source=(
            f"aggregated-from-{source_path.name}#sha256:{source.fingerprint[:12]}"
            f"#{census.ratio}-of-{census.ratio}-{SOURCE_TIMEFRAME.value}-legs"
        ),
        candles=census.bars,
    )
    directory.mkdir(parents=True, exist_ok=True)
    write_dataset(dataset_path(directory, source.symbol, target, stamp), dataset)
    return dataset


def slot_times(bucket: datetime, ratio: int) -> list[datetime]:
    step = timedelta(seconds=SOURCE_TIMEFRAME.seconds)
    return [bucket + step * index for index in range(ratio)]


def unfolded_context(
    source: CandleDataset, census: Census, calendar: Calendars, target: Timeframe
) -> dict[str, object]:
    """Split the legs no bar could be folded from into boundary, closure, hole and shut."""
    ratio = census.ratio
    step = timedelta(seconds=SOURCE_TIMEFRAME.seconds)
    present = {candle.open_time for candle in source.candles}
    head = grid_floor(source.start, target.seconds)
    tail = grid_floor(source.candles[-1].open_time, target.seconds)
    absent = {
        at
        for bucket in [bucket for bucket, _ in census.partial] + list(census.empty)
        for at in slot_times(bucket, ratio)
        if at not in present
    }

    runs: list[list[datetime]] = []
    current: list[datetime] = []
    at = head
    end = tail + step * (ratio - 1)
    while at <= end:
        if at in absent:
            current.append(at)
        elif current:
            runs.append(current)
            current = []
        at += step
    if current:
        runs.append(current)

    boundary = closure = hole = shut = 0
    holes: list[datetime] = []
    for run in runs:
        closed_inside = any(calendar.status_at(moment) is not SlotStatus.OPEN for moment in run)
        for moment in run:
            if calendar.status_at(moment) is not SlotStatus.OPEN:
                shut += 1
                continue
            bucket = grid_floor(moment, target.seconds)
            if bucket in {head, tail}:
                boundary += 1
            elif closed_inside:
                closure += 1
            else:
                hole += 1
                holes.append(moment)
    require(
        boundary + closure + hole + shut == census.legs_unfolded,
        f"{census.legs_unfolded} unfolded legs but {boundary}+{closure}+{hole}+{shut} classified",
    )
    return {
        "boundary": boundary,
        "closure": closure,
        "hole": hole,
        "shut": shut,
        "absent_runs": len(runs),
        "hole_slots": [moment.isoformat() for moment in holes],
    }


def sidecar_record(
    source: CandleDataset,
    source_path: Path,
    frozen: CandleDataset,
    census: Census,
    calendar: Calendars,
    context: dict[str, object],
) -> dict[str, object]:
    """The visible hole: every dropped bucket, the leg times it lacks, and who closed the market."""
    ratio = census.ratio
    target_step = timedelta(seconds=frozen.timeframe.seconds)
    head = grid_floor(source.start, frozen.timeframe.seconds)
    tail = grid_floor(source.candles[-1].open_time, frozen.timeframe.seconds)

    partial = []
    for bucket, present in census.partial:
        missing = [at for index, at in enumerate(slot_times(bucket, ratio)) if index not in present]
        partial.append(
            {
                "bucket": bucket.isoformat(),
                "legs_present": len(present),
                "legs_missing": ratio - len(present),
                "missing_legs": [at.isoformat() for at in missing],
                "boundary": bucket in {head, tail},
            }
        )

    runs: list[dict[str, object]] = []
    for bucket in census.empty:
        if runs and bucket - datetime.fromisoformat(str(runs[-1]["end"])) == target_step:
            runs[-1]["end"] = bucket.isoformat()
            runs[-1]["buckets"] = int(runs[-1]["buckets"]) + 1
            continue
        runs.append({"start": bucket.isoformat(), "end": bucket.isoformat(), "buckets": 1})

    return {
        "rule": (
            f"a {SOURCE_TIMEFRAME} bucket of {ratio} legs is written only when all {ratio} legs "
            f"sit at their exact slot; otherwise the bucket is dropped, never filled"
        ),
        "dataset": {
            "dataset_id": frozen.dataset_id,
            "timeframe": frozen.timeframe.value,
            "bars": frozen.bars,
            "start": frozen.start.isoformat(),
            "end": frozen.end.isoformat(),
            "fingerprint": frozen.fingerprint,
        },
        "source": {
            "file": source_path.name,
            "dataset_id": source.dataset_id,
            "timeframe": source.timeframe.value,
            "bars": source.bars,
            "start": source.start.isoformat(),
            "end": source.end.isoformat(),
            "fingerprint": source.fingerprint,
        },
        "calendar": {
            "symbol": calendar.symbol,
            "recent_weeks": CALENDAR_WEEKS,
            "long_weeks": calendar.long_weeks,
            "long_open_ratio": CALENDAR_LONG_RATIO,
            "definition": (
                "a slot is open only when the four-week calendar and the whole-span calendar "
                "(open_ratio 0.9) both mark it open"
            ),
        },
        "legs_per_bar": ratio,
        "expected_buckets": census.expected,
        "bars": len(census.bars),
        "partial": {"count": len(partial), "buckets": partial},
        "empty": {"count": len(census.empty), "runs": runs},
        "legs": {
            "source": source.bars,
            "folded": census.legs_folded,
            "in_partial_buckets": census.legs_in_partial,
            "expected_slots": census.expected * ratio,
            "unfolded": census.legs_unfolded,
            "unfolded_boundary": context["boundary"],
            "unfolded_closure": context["closure"],
            "unfolded_hole": context["hole"],
            "unfolded_shut": context["shut"],
            "absent_runs": context["absent_runs"],
            "hole_slots": context["hole_slots"],
            "conserved": census.legs_folded + census.legs_in_partial == source.bars,
        },
    }


# ------------------------------------------------------------------------- verification


@dataclass(frozen=True)
class Row:
    """One frozen dataset, measured on the file that was actually reloaded."""

    symbol: str
    timeframe: Timeframe
    ratio: int
    path: Path
    bars: int
    start: datetime
    end: datetime
    fingerprint: str
    source_bars: int
    partial: int
    partial_boundary: int
    empty: int
    gaps: int
    legs_unfolded: int
    legs_boundary: int
    legs_closure: int
    legs_hole: int
    legs_shut: int

    @property
    def dropped(self) -> int:
        return self.partial + self.empty


def check_frozen(
    dataset: CandleDataset, census: Census, source: CandleDataset, source_path: Path
) -> None:
    """Re-derive every frozen bar from the source legs, for the bytes that are on disk."""
    ratio = census.ratio
    grid_step = timedelta(seconds=SOURCE_TIMEFRAME.seconds)
    legs: dict[datetime, list[Candle]] = defaultdict(list)
    for candle in source.candles:
        legs[grid_floor(candle.open_time, dataset.timeframe.seconds)].append(candle)
    require(
        len(dataset.candles) == len(census.bars),
        f"{dataset.dataset_id}: {len(dataset.candles)} bars on disk, {len(census.bars)} folded",
    )
    for bar in dataset.candles:
        present = legs.get(bar.open_time, [])
        require(
            len(present) == ratio,
            f"{dataset.dataset_id}: bar {bar.open_time.isoformat()} holds {len(present)} legs of "
            f"{ratio} -- an incomplete bar was written",
        )
        expected = [bar.open_time + grid_step * index for index in range(ratio)]
        require(
            [leg.open_time for leg in present] == expected,
            f"{dataset.dataset_id}: bar {bar.open_time.isoformat()} legs are not the {ratio} "
            f"consecutive slots the bar claims",
        )
        require(
            bar.open == present[0].open and bar.close == present[-1].close,
            f"{dataset.dataset_id}: bar {bar.open_time.isoformat()} open/close differ from legs",
        )
        require(
            bar.high == max(leg.high for leg in present)
            and bar.low == min(leg.low for leg in present),
            f"{dataset.dataset_id}: bar {bar.open_time.isoformat()} high/low differ from legs",
        )
    grid = _bucket_span(source, dataset.timeframe)
    require(
        census.expected == grid,
        f"{dataset.dataset_id}: census covers {census.expected} buckets, the target grid holds "
        f"{grid}",
    )
    runs = _gap_runs(dataset)
    interior = _interior_dropped(census, dataset)
    require(
        runs == interior,
        f"{dataset.dataset_id}: {runs} time discontinuities on disk but {interior} interior "
        f"dropped run(s) -- a hole was hidden or a bar was filled",
    )
    require(
        source_path.exists(),
        f"{dataset.dataset_id}: source {source_path.name} is gone, provenance is unverifiable",
    )


def _bucket_span(source: CandleDataset, target: Timeframe) -> int:
    first = grid_floor(source.start, target.seconds)
    last = grid_floor(source.candles[-1].open_time, target.seconds)
    return int((last - first).total_seconds()) // target.seconds + 1


def _gap_runs(dataset: CandleDataset) -> int:
    step = timedelta(seconds=dataset.timeframe.seconds)
    return sum(
        1
        for earlier, later in zip(dataset.candles, dataset.candles[1:], strict=False)
        if later.open_time - earlier.open_time != step
    )


def _interior_dropped(census: Census, dataset: CandleDataset) -> int:
    """Runs of *consecutive* dropped buckets strictly between the first and the last frozen bar.

    A run is what leaves one time discontinuity on disk, so it is the number of holes the
    frozen series must show. A run ending against the head or the tail of the grid adds no
    discontinuity -- there is no bar on that side to be separated from.
    """
    first, last = dataset.candles[0].open_time, dataset.candles[-1].open_time
    step = timedelta(seconds=dataset.timeframe.seconds)
    dropped = sorted([bucket for bucket, _ in census.partial] + list(census.empty))
    runs = 0
    previous: datetime | None = None
    for bucket in dropped:
        if bucket <= first or bucket >= last:
            previous = None
            continue
        if previous is None or bucket - previous != step:
            runs += 1
        previous = bucket
    return runs


def measure(
    dataset: CandleDataset,
    census: Census,
    context: dict[str, object],
    path: Path,
    source: CandleDataset,
) -> Row:
    head = grid_floor(source.start, dataset.timeframe.seconds)
    tail = grid_floor(source.candles[-1].open_time, dataset.timeframe.seconds)
    return Row(
        symbol=dataset.symbol,
        timeframe=dataset.timeframe,
        ratio=census.ratio,
        path=path,
        bars=dataset.bars,
        start=dataset.start,
        end=dataset.end,
        fingerprint=dataset.fingerprint,
        source_bars=census.source_bars,
        partial=len(census.partial),
        partial_boundary=sum(1 for bucket, _ in census.partial if bucket in {head, tail}),
        empty=len(census.empty),
        gaps=_gap_runs(dataset),
        legs_unfolded=census.legs_unfolded,
        legs_boundary=int(context["boundary"]),
        legs_closure=int(context["closure"]),
        legs_hole=int(context["hole"]),
        legs_shut=int(context["shut"]),
    )


# --------------------------------------------------------------------------- walk-forward


def deployed_manifests() -> list[StrategyManifest]:
    """The manifests of `config/strategies/`, read from configuration, never restated."""
    manifests = [
        StrategyManifest.model_validate(read_yaml(path).data)
        for path in sorted(MANIFEST_DIR.glob("*.yaml"))
    ]
    require(bool(manifests), f"no manifest under {MANIFEST_DIR}")
    return manifests


def feasibility(dataset: CandleDataset) -> list[dict[str, object]]:
    """Whether the repository's own walk-forward plan yields folds on this series."""
    out: list[dict[str, object]] = []
    for manifest in deployed_manifests():
        plan = walk_forward_plan_for(manifest)
        folds = walk_forward(dataset.candles, plan)
        try:
            split = split_dataset(dataset, token=SEALED_UNLOCK)
            validation = len(split.validation)
            detail = (
                f"split {len(split.train)}/{validation}/{split.holdout.size}, validation "
                f"{'serves' if validation >= manifest.history_bars else 'too short for'} "
                f"history {manifest.history_bars}"
            )
        except ValueError as error:
            validation = 0
            detail = f"split refused: {error}"
        out.append(
            {
                "manifest": manifest.ref,
                "market_match": dataset.symbol in manifest.allowed_symbols,
                "history_bars": manifest.history_bars,
                "fold_bars": plan.train_bars + plan.validation_bars,
                "folds": len(folds),
                "played": len(folds) > 0,
                "validation_bars": validation,
                "detail": detail,
            }
        )
    return out


# ------------------------------------------------------------------------------- printing


def markdown_datasets(rows: list[Row], title: str) -> str:
    head = (
        f"### {title}\n\n| Marché | UT | Bougies écrites | Fenêtre UTC | Empreinte SHA-256 | "
        "Bougies écartées (incomplet / vide) | Trous dans la série | Bougies M15 non pliées | "
        "dont bords d'export | dont bords de fermeture élargie | dont non expliqués | "
        "dont marché fermé | Fichier |"
    )
    lines = [head, "|" + "---|" * 13]
    for row in rows:
        lines.append(
            f"| {row.symbol} | {row.timeframe.value} | {row.bars} | {row.start:%Y-%m-%d %H:%M} -> "
            f"{row.end:%Y-%m-%d %H:%M} | `{row.fingerprint[:16]}...` | {row.partial} / "
            f"{row.empty} | {row.gaps} | {row.legs_unfolded} | {row.legs_boundary} | "
            f"{row.legs_closure} | {row.legs_hole} | {row.legs_shut} | `{row.path.name}` |"
        )
    return "\n".join(lines)


def markdown_folds(datasets: list[CandleDataset], title: str) -> str:
    head = (
        f"### {title}\n\n| Marché | UT | Bougies | Manifeste du marché | Historique déclaré | "
        "Bougies par fold | Folds jouables | Validation | Verdict |"
    )
    lines = [head, "|" + "---|" * 9]
    for dataset in datasets:
        for item in feasibility(dataset):
            if not item["market_match"]:
                continue
            verdict = "utilisable" if item["played"] else "AUCUN FOLD"
            lines.append(
                f"| {dataset.symbol} | {dataset.timeframe.value} | {dataset.bars} | "
                f"{item['manifest']} | {item['history_bars']} | {item['fold_bars']} | "
                f"{item['folds']} | {item['detail']} | {verdict} |"
            )
    return "\n".join(lines)


def print_rows(rows: list[Row]) -> None:
    for row in rows:
        print(
            f"{row.symbol:<7} {row.timeframe.value:<3} bars={row.bars:<6} "
            f"{row.start:%Y-%m-%d %H:%M} -> {row.end:%Y-%m-%d %H:%M} "
            f"sha256={row.fingerprint[:16]}"
        )
        print(
            f"        dropped={row.dropped} (partial {row.partial}, empty {row.empty})  "
            f"m15_unfolded={row.legs_unfolded} = boundary {row.legs_boundary} + closure "
            f"{row.legs_closure} + unexplained {row.legs_hole} + market shut "
            f"{row.legs_shut}  gaps_on_disk={row.gaps}  source_bars={row.source_bars}"
        )


def print_feasibility(datasets: list[CandleDataset]) -> None:
    print("\nWALK-FORWARD FEASIBILITY (plan built by tradingagent.research.campaign)")
    for dataset in datasets:
        for item in feasibility(dataset):
            mark = "*" if item["market_match"] else " "
            verdict = "USABLE" if item["played"] else "NO FOLD"
            print(
                f" {mark}{dataset.symbol:<7} {dataset.timeframe.value:<3} "
                f"{item['manifest']:<22} history={item['history_bars']:<4} "
                f"fold_bars={item['fold_bars']:<5} folds={item['folds']:<3} {verdict:<8} "
                f"{item['detail']}"
            )
    print(" * = the manifest allows this market")


# ------------------------------------------------------------------------------ commands


def load_sources(
    directory: Path,
) -> tuple[dict[str, CandleDataset], dict[str, Path], dict[str, Calendars]]:
    sources: dict[str, CandleDataset] = {}
    paths: dict[str, Path] = {}
    calendars: dict[str, Calendars] = {}
    for path in source_files(directory):
        dataset = load_dataset(path)
        require(
            dataset.timeframe is SOURCE_TIMEFRAME,
            f"{path.name} is {dataset.timeframe}, expected {SOURCE_TIMEFRAME}",
        )
        require(dataset.symbol not in sources, f"two source datasets for {dataset.symbol}")
        sources[dataset.symbol] = dataset
        paths[dataset.symbol] = path
        weeks = max(1, int((dataset.end - dataset.start).days / 7) + 1)
        calendars[dataset.symbol] = Calendars(
            recent=learn_calendar(
                dataset.symbol, dataset.candles, dataset.end, weeks=CALENDAR_WEEKS
            ),
            long=learn_calendar(
                dataset.symbol,
                dataset.candles,
                dataset.end,
                weeks=weeks,
                open_ratio=CALENDAR_LONG_RATIO,
            ),
            long_weeks=weeks,
        )
    return sources, paths, calendars


def command_build(source_directory: Path, output: Path, rebuild: bool) -> int:
    sources, paths, calendars = load_sources(source_directory)
    for target in TARGETS:
        directory = target_directory(output, target)
        for symbol in sorted(sources):
            source = sources[symbol]
            path = dataset_path(directory, symbol, target, stamp_of(source))
            if rebuild:
                for victim in (
                    path,
                    path.with_suffix(".manifest.json"),
                    path.with_suffix(".aggregation.json"),
                ):
                    resolved = victim.resolve()
                    require(
                        resolved.parent == directory.resolve(),
                        f"refusing to delete {resolved}, outside {directory}",
                    )
                    if resolved.exists():
                        resolved.unlink()
                        print(f"removed {resolved.name}")
            census = aggregate(source, target)
            frozen = freeze(source, paths[symbol], census, target, directory)
            context = unfolded_context(source, census, calendars[symbol], target)
            record = sidecar_record(
                source, paths[symbol], frozen, census, calendars[symbol], context
            )
            path.with_suffix(".aggregation.json").write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            path.with_suffix(".manifest.json").write_text(
                json.dumps(frozen.manifest(calendars[symbol].recent).to_dict(), indent=2) + "\n",
                encoding="utf-8",
            )
            print(
                f"written {path.name}: bars={frozen.bars} partial={len(census.partial)} "
                f"empty={len(census.empty)} unfolded={census.legs_unfolded} "
                f"holes={context['hole']} fingerprint={frozen.fingerprint}"
            )
    return 0


def command_verify(source_directory: Path, output: Path, markdown: Path | None, title: str) -> int:
    sources, paths, calendars = load_sources(source_directory)
    rows: list[Row] = []
    datasets: list[CandleDataset] = []
    for target in TARGETS:
        directory = target_directory(output, target)
        require(directory.is_dir(), f"{directory} does not exist: run build first")
        for symbol, dataset in sorted(DatasetStore(directory).load_all().items()):
            source = sources[symbol]
            census = aggregate(source, target)
            check_frozen(dataset, census, source, paths[symbol])
            context = unfolded_context(source, census, calendars[symbol], target)
            frozen_path = dataset_path(directory, symbol, target, stamp_of(source))
            rows.append(measure(dataset, census, context, frozen_path, source))
            sidecar = frozen_path.with_suffix(".aggregation.json")
            require(sidecar.exists(), f"{sidecar.name} is missing: the hole census is unrecorded")
            claim = json.loads(sidecar.read_text(encoding="utf-8"))
            fresh = sidecar_record(
                source, paths[symbol], dataset, census, calendars[symbol], context
            )
            require(claim == fresh, f"{sidecar.name} disagrees with a fresh census")
            datasets.append(dataset)
    print("every frozen dataset reloaded, fingerprint recomputed, legs re-derived from source")
    print_rows(rows)
    print_feasibility(datasets)
    tables = markdown_datasets(rows, title) + "\n\n" + markdown_folds(datasets, title)
    if markdown is not None:
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(tables + "\n", encoding="utf-8")
        print(f"\ntables written to {markdown}")
    print("\n" + tables)
    return 0


def self_test() -> int:
    """Prove the golden rule on crafted series before trusting it on real data."""
    failures: list[str] = []

    def check(condition: bool, label: str) -> None:
        print(f"  [{'ok' if condition else 'FAIL'}] {label}")
        if not condition:
            failures.append(label)

    def leg(slot: int, price: float) -> Candle:
        return Candle(
            timeframe=SOURCE_TIMEFRAME,
            open_time=datetime(2026, 1, 5, tzinfo=UTC) + timedelta(minutes=15 * slot),
            open=price,
            high=price + 0.5,
            low=price - 0.5,
            close=price + 0.25,
        )

    def crafted(slots: list[int], dataset_id: str) -> CandleDataset:
        return CandleDataset(
            dataset_id=dataset_id,
            symbol="TEST",
            timeframe=SOURCE_TIMEFRAME,
            source="crafted",
            candles=tuple(leg(slot, 100.0 + slot) for slot in slots),
        )

    # Hour 00 complete, one leg missing in hour 01, hours 02-03 complete, hour 04 absent,
    # hour 05 complete.
    slots = [0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 20, 21, 22, 23]
    source = crafted(slots, "self-test-H1")
    print("self-test H1: four legs required, one missing in 01:00, hour 04:00 absent")
    census = aggregate(source, Timeframe.H1)
    written = [bar.open_time.hour for bar in census.bars]
    check(written == [0, 2, 3, 5], f"only the complete hours are written: {written}")
    check(1 not in written, "the 01:00 bucket holding three of four legs is absent")
    check(4 not in written, "the bucket with no leg at all is absent")
    check(
        census.partial == ((datetime(2026, 1, 5, 1, tzinfo=UTC), (0, 2, 3)),),
        "the partial bucket is counted with the exact slots it holds (01:15 missing)",
    )
    check(len(census.empty) == 1, "the empty bucket is counted once")
    check(census.legs_lost == 1, f"one leg is reported lost, got {census.legs_lost}")
    check(census.expected == 6, f"the grid holds six buckets, got {census.expected}")
    check(census.legs_folded + census.legs_in_partial == source.bars, "legs are conserved")
    hour0 = census.bars[0]
    check(
        (hour0.open, hour0.close, hour0.high, hour0.low) == (100.0, 103.25, 103.5, 99.5),
        "the folded OHLC is the legs' open/close/max high/min low",
    )

    print("self-test H4: same source, sixteen legs required")
    h4 = aggregate(source, Timeframe.H4)
    check(h4.bars == (), "no H4 bar is written from a series without sixteen consecutive legs")
    check(
        any(len(present) == 4 for _, present in h4.partial),
        "a bucket holding four legs exists and is dropped, not promoted to an H4 bar",
    )
    check(
        h4.legs_folded + h4.legs_in_partial + h4.legs_unfolded == h4.expected * h4.ratio,
        "every slot of the target grid is folded, held in a partial bucket, or unfolded",
    )

    print("self-test rejections")
    check(target_ratio(Timeframe.H4, Timeframe.H1) == 4, "H1 is a legal source for H4 (4 legs)")
    try:
        target_ratio(Timeframe.H1, Timeframe.H4)
    except RuleError as error:
        check(True, f"a source coarser than the target is refused ({error})")
    else:
        check(False, "a source coarser than the target is refused")
    try:
        aggregate(
            CandleDataset(
                dataset_id="self-test-offgrid",
                symbol="TEST",
                timeframe=SOURCE_TIMEFRAME,
                source="crafted",
                candles=(leg(0, 1.0), _shifted(leg(1, 2.0))),
            ),
            Timeframe.H1,
        )
    except RuleError as error:
        check(True, f"a leg off the M15 grid is refused ({error})")
    else:
        check(False, "a leg off the M15 grid is refused")

    print(f"self-test: {'PASS' if not failures else f'FAIL ({len(failures)})'}")
    return 1 if failures else 0


def _shifted(candle: Candle, minutes: int = 5) -> Candle:
    return Candle(
        timeframe=candle.timeframe,
        open_time=candle.open_time + timedelta(minutes=minutes),
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("self-test", "build", "verify"))
    parser.add_argument("--source", type=Path, default=SOURCE_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--markdown", type=Path, default=None)
    parser.add_argument("--title", default="Jeux gelés H1/H4")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="remove this tool's own output files before writing (raw files stay immutable)",
    )
    args = parser.parse_args()
    if args.command == "self-test":
        return self_test()
    if args.command == "build":
        return command_build(args.source, args.output, args.rebuild)
    return command_verify(args.source, args.output, args.markdown, args.title)


if __name__ == "__main__":
    raise SystemExit(main())
