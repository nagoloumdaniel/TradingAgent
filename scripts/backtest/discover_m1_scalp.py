"""Mesurer la famille scalp_triple_filter sur le jeu M1 gelé, M5 construit par agrégation.

    uv run python scripts/backtest/discover_m1_scalp.py
    uv run python scripts/backtest/discover_m1_scalp.py --families scalp_triple_filter
    uv run python scripts/backtest/discover_m1_scalp.py --datasets docs/research/datasets/M1

POURQUOI CE SCRIPT EXISTE, ET CE QU'IL N'EST PAS
------------------------------------------------
La règle mesurée ici décide sur M1 et lit un filtre EMA sur M5 : le harnais a besoin des
deux séries, et le magasin gelé M1 n'en contient qu'une. Le script agrège donc M1 en M5
avant de lancer la découverte — il ne fabrique aucune bougie, il regroupe celles du jeu.

**Ce que ce lanceur ne peut pas faire.** Le courtier ne sert pas de M1 au-delà du
2026-06-23 pour l'or (plafond de bougies du terminal atteint, voir
`docs/reports/2026-10-03-mt5-capabilities.md`). Le jeu gelé couvre donc **12,8 jours**, et
aucun résultat de ce script ne peut valider ou invalider un backtest publié sur 2020-2026 :
la fenêtre est trop courte pour porter un régime de marché, pas seulement trop courte en
nombre de bougies. Un backtest de 12 jours produit des milliers d'opérations qui ne sont
pas des observations indépendantes.

Découvrir n'est pas promouvoir : ce script n'écrit aucun manifeste dans
`config/strategies/` et ne touche pas `strategies/registry.py`.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from tradingagent.backtest.datasets import CandleDataset, DatasetStore
from tradingagent.research.discovery import FAMILIES as ALL_FAMILIES
from tradingagent.research.discovery import (
    DiscoveryProtocol,
    DiscoveryReport,
    FamilyTemplate,
    default_grid,
    discover,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASETS = ROOT / "docs" / "research" / "datasets" / "M1"
DEFAULT_OUTPUT = ROOT / "docs" / "research" / "scalp-m1"
FAMILY = "scalp_triple_filter"


def select_families(names: str | None) -> tuple[FamilyTemplate, ...]:
    if not names:
        return ALL_FAMILIES
    wanted = tuple(part.strip() for part in names.split(",") if part.strip())
    known = {template.family for template in ALL_FAMILIES}
    unknown = sorted(set(wanted) - known)
    if unknown:
        raise SystemExit(f"unknown family/families {unknown}; known: {sorted(known)}")
    return tuple(template for template in ALL_FAMILIES if template.family in wanted)


def load_markets(directory: Path) -> dict[str, CandleDataset]:
    found = DatasetStore(directory).load_all()
    if not found:
        raise SystemExit(f"no *.jsonl dataset found in {directory}")
    return {dataset.symbol: dataset for dataset in found.values()}


def print_report(report: DiscoveryReport) -> None:
    print("== Jeux de donnees ==")
    for market in report.markets:
        if market.skipped is not None:
            print(f"  {market.market:10s} INUTILISABLE: {market.skipped}")
            continue
        print(
            f"  {market.market:10s} {market.dataset_id:26s} {market.bars:6d} bougies "
            f"train={market.train_bars} validation={market.validation_bars} "
            f"scelle={market.holdout_bars} plis={market.walk_forward_folds} "
            f"ouvertures du scelle={market.holdout_unlocks}"
        )
    print("== Familles ==")
    for family in report.families:
        if not family.tested:
            continue
        print(f"  {family.family} — {family.description}")
        print(
            f"    testes {family.tested} | retenus avant correction "
            f"{family.retained_before_correction} | apres correction {family.retained} "
            f"| ecartes {family.discarded}"
        )
        for cause, count in family.failures.items():
            print(f"      - {cause.value:24s} {count:4d}  ({cause.description})")
    multiple = report.multiple_testing
    print("== Selection multiple ==")
    print(
        f"  hypotheses {multiple.hypotheses} | seuil de Bonferroni "
        f"{multiple.bonferroni_threshold:.6f} | avant {multiple.discoveries_before} "
        f"-> apres {multiple.discoveries_after}"
    )
    print("== Candidats retenus apres correction (aucun n'est promu) ==")
    retained = report.retained()
    if not retained:
        print("  aucun : c'est un resultat, pas un echec du laboratoire")
    for candidate in retained:
        performance = candidate.out_of_sample
        net = "n/a" if performance is None else f"{performance.net_profit}"
        trades = 0 if performance is None else performance.trades
        p_value = "n/a" if candidate.p_value is None else f"{candidate.p_value:.4f}"
        win = "n/a" if performance is None else f"{performance.win_rate:.4f}"
        print(
            f"  {candidate.market:10s} {candidate.label:26s} p={p_value} "
            f"hors-echantillon={net} operations={trades} winrate={win}"
        )
    print("== Ecartes, une ligne par cause ==")
    for cause, count in report.failures_by_cause().items():
        print(f"  {cause.value:24s} {count:4d}  ({cause.description})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", type=Path, default=DEFAULT_DATASETS)
    parser.add_argument("--families", type=str, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    datasets = load_markets(args.datasets)
    print(f"jeux charges depuis {args.datasets} : {sorted(datasets)}")

    report = discover(
        datasets, default_grid(), DiscoveryProtocol(), families=select_families(args.families)
    )
    print_report(report)

    args.output.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "scripts/backtest/discover_m1_scalp.py",
        "datasets": {
            market: {
                "dataset_id": dataset.dataset_id,
                "fingerprint": dataset.fingerprint,
                "bars": dataset.bars,
                "timeframe": dataset.timeframe.value,
            }
            for market, dataset in sorted(datasets.items())
        },
        "report": report.to_dict(),
    }
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    path = args.output / f"{stamp}-discovery-scalp-m1.json"
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"rapport ecrit dans {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
