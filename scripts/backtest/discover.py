"""Run the multi-family discovery laboratory and print the honest failure table.

Examples
--------
    uv run python scripts/backtest/discover.py
    uv run python scripts/backtest/discover.py --datasets docs/research/datasets
    uv run python scripts/backtest/discover.py --bars 2500 --seed 20261007
    uv run python scripts/backtest/discover.py --families momentum,mean_reversion,breakout

Without ``--datasets``, seeded synthetic M15 series are generated (no long Deriv history is
versioned yet). With ``--datasets``, every frozen ``*.jsonl`` dataset of TASK-060 is used
as-is, fingerprint verified by the loader.

**Discovering is not promoting.** This script writes a JSON report under
``docs/research/``, prints a summary, and stops there: it never writes a manifest into
``config/strategies/`` and never edits ``strategies/registry.py``. A candidate becomes
executable only once the Lead promotes it.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from tradingagent.backtest.datasets import (
    CandleDataset,
    DatasetStore,
    SyntheticRegime,
    synthetic_dataset,
)
from tradingagent.backtest.randomness import DeterministicRandom
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.discovery import (
    FAMILIES,
    DiscoveryProtocol,
    DiscoveryReport,
    FamilyTemplate,
    default_grid,
    discover,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "docs" / "research"
TIMEFRAME = Timeframe.M15
START = datetime(2026, 1, 5, tzinfo=UTC)
DEFAULT_BARS = 1_500
DEFAULT_SEED = 20_261_007
MARKETS = ("frxXAUUSD", "cryBTCUSD", "cryETHUSD")
START_PRICES = {"frxXAUUSD": 2000.0, "cryBTCUSD": 60_000.0, "cryETHUSD": 3_000.0}
DRIFT = {"frxXAUUSD": 0.00002, "cryBTCUSD": 0.00004, "cryETHUSD": 0.00003}
VOLATILITY = {"frxXAUUSD": 0.0012, "cryBTCUSD": 0.0015, "cryETHUSD": 0.0012}
BETA = {"frxXAUUSD": 0.0006, "cryBTCUSD": 0.0025, "cryETHUSD": 0.0025}


def synthetic_markets(bars: int, seed: int) -> dict[str, CandleDataset]:
    """Three seeded markets sharing one factor, so no family can exploit a lucky tape."""
    factor_stream = DeterministicRandom(seed)
    factor = [factor_stream.gauss() for _ in range(bars)]
    datasets: dict[str, CandleDataset] = {}
    for index, market in enumerate(MARKETS):
        regimes = (SyntheticRegime(bars=bars, drift=DRIFT[market], volatility=VOLATILITY[market]),)
        datasets[market] = synthetic_dataset(
            f"synthetic-m15-{bars}",
            market,
            TIMEFRAME,
            START,
            regimes,
            seed=seed + index,
            start_price=START_PRICES[market],
            common_returns=factor,
            beta=BETA[market],
            decimals=2,
        )
    return datasets


def load_markets(directory: Path) -> dict[str, CandleDataset]:
    found = DatasetStore(directory).load_all()
    if not found:
        raise SystemExit(f"no *.jsonl dataset found in {directory}")
    return {dataset.symbol: dataset for dataset in found.values()}


def select_families(names: str | None) -> tuple[FamilyTemplate, ...]:
    if not names:
        return FAMILIES
    wanted = tuple(part.strip() for part in names.split(",") if part.strip())
    known = {template.family for template in FAMILIES}
    unknown = sorted(set(wanted) - known)
    if unknown:
        raise SystemExit(f"unknown family/families {unknown}; known: {sorted(known)}")
    return tuple(template for template in FAMILIES if template.family in wanted)


def print_report(report: DiscoveryReport) -> None:
    print("== Jeux de données ==")
    for market in report.markets:
        if market.skipped is not None:
            print(f"  {market.market:12s} {market.dataset_id:20s} INUTILISABLE: {market.skipped}")
            continue
        print(
            f"  {market.market:12s} {market.dataset_id:20s} {market.bars:5d} bougies "
            f"train={market.train_bars} validation={market.validation_bars} "
            f"scellé={market.holdout_bars} plis={market.walk_forward_folds} "
            f"ouverture du scellé={market.holdout_unlocks} "
            f"empreinte={market.fingerprint[:12]}…"
        )
    print("== Familles explorées (découvrir n'est pas promouvoir) ==")
    for family in report.families:
        print(f"  {family.family} — {family.description}")
        print(
            f"    candidats testés {family.tested} | retenus {family.retained} "
            f"| écartés {family.discarded}"
        )
        for cause, count in family.failures.items():
            print(f"      - {cause.value:24s} {count:4d}  ({cause.description})")
    print("== Candidats retenus (aucun n'est promu à ce stade) ==")
    retained = report.retained()
    if not retained:
        print("  aucun : c'est un résultat, pas un échec du laboratoire")
    for candidate in retained:
        performance = candidate.out_of_sample
        net = "n/a" if performance is None else f"{performance.net_profit}"
        trades = 0 if performance is None else performance.trades
        print(
            f"  {candidate.market:12s} {candidate.family:20s} {candidate.label:24s} "
            f"hors-échantillon={net} opérations={trades}"
        )
    totals = report.failures_by_cause()
    print("== Écartés, une ligne par cause ==")
    for cause, count in totals.items():
        print(f"  {cause.value:24s} {count:4d}  ({cause.description})")
    print(
        f"== Total : testés {len(report.candidates)} | retenus {len(retained)} "
        f"| écartés {len(report.discarded())} =="
    )
    print("Rappel : une découverte n'est pas une promotion; seul le Lead promeut.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--datasets",
        type=Path,
        default=None,
        help="directory of frozen *.jsonl datasets; seeded synthetic data is used otherwise",
    )
    parser.add_argument("--bars", type=int, default=DEFAULT_BARS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--families", type=str, default=None, help="comma-separated family ids")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    families = select_families(args.families)
    if args.datasets is not None:
        datasets = load_markets(args.datasets)
        print(f"loaded {len(datasets)} frozen dataset(s) from {args.datasets}")
    else:
        datasets = synthetic_markets(args.bars, args.seed)
        print(
            f"no dataset directory given: generated {len(datasets)} seeded synthetic series "
            f"({args.bars} bars, seed {args.seed})"
        )

    report = discover(datasets, default_grid(), DiscoveryProtocol(), families=families)
    print_report(report)

    args.output.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "scripts/backtest/discover.py",
        "datasets": {
            market: {
                "dataset_id": dataset.dataset_id,
                "fingerprint": dataset.fingerprint,
                "source": dataset.source,
                "bars": dataset.bars,
            }
            for market, dataset in sorted(datasets.items())
        },
        "report": report.to_dict(),
    }
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    json_path = args.output / f"{stamp}-discovery.json"
    json_path.write_text(
        json.dumps(payload, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"report written to {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
