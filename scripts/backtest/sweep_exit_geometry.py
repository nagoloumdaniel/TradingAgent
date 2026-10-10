"""La géométrie de sortie est-elle la cause ? Balayage a regle de decision fixee d'avance.

**La question, posee par la mesure precedente.** `vwap_pullback@1.1.0` perd sur les deux
marches : PF net 0,68 (BTC) et 0,55 (or) en validation, pour un gain moyen de 6 EUR contre une
perte moyenne de 11 EUR. Une reussite de 55 % ne suffit pas a ce rapport. La detection produit
pourtant 709 et 656 operations : la regle trouve des trades, c'est la sortie qui ne paie pas.

**La regle de decision est ecrite AVANT de regarder les chiffres, et ne sera pas ajustee.**

1. Chaque geometrie est mesuree sur les deux marches avec la meme voie (`run_campaign`, couts du
   depot, sortie au PREMIER objectif comme le fait la production).
2. La selection se fait sur l'**entrainement seul** : c'est la geometrie de meilleur facteur de
   profit net en entrainement, moyennee sur les deux marches.
3. Elle n'est **adoptee** que si elle atteint PF net >= 1,00 sur la **validation des deux
   marches**. Un seul marche sous 1,00 et rien n'est adopte.
4. Si aucune geometrie n'atteint les deux, le rapport le dit et la geometrie d'origine reste.

Le point 3 est la partie qui compte : selectionner sur l'entrainement puis exiger la validation
est ce qui empeche de choisir la geometrie qui a eu de la chance. Balayer des geometries est
exactement l'activite que le protocole walk-forward du depot existe pour encadrer.

    uv run python scripts/backtest/sweep_exit_geometry.py
    uv run python scripts/backtest/sweep_exit_geometry.py --ref vwap_pullback@1.1.0 --bars 20000

Lecture seule : rien n'est promu, rien n'est ecrit dans `config/`, le jeu scelle reste ferme.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure_strategy import _load_current_stats

ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "docs" / "research" / "datasets-volume"
OUTPUT_DIR = ROOT / "docs" / "research" / "measurements"

#: (TP1, TP2, multiplicateur de stop) — une geometrie est un triplet, pas un reglage isole.
#: `(0.8, 1.5, 1.5)` est celle d'origine, mesuree en premier : c'est la reference a battre.
GEOMETRIES: tuple[tuple[float, float, float], ...] = (
    (0.8, 1.5, 1.5),  # l'originale
    (1.0, 2.0, 1.5),
    (1.5, 3.0, 1.5),  # la meilleure de la recherche du 2026-10-09, mesuree autrement
    (2.0, 4.0, 1.5),
    (1.0, 2.0, 1.0),  # stop plus serre : la spec exigeait de gagner 2 fois sur 3
    (1.5, 3.0, 1.0),
    (1.0, 2.0, 2.0),  # stop plus large : moins de sorties sur le bruit
)

ADOPTION_FLOOR = 1.00


def _variant(ref: str, first: float, final: float, stop: float) -> Any:
    """The candidate, with `write_candidate`'s guarantee: nothing lands where the agent loads."""
    import yaml

    from tradingagent.strategies.manifest import StrategyManifest
    from tradingagent.strategies.registry import REGISTRY

    document = yaml.safe_load((ROOT / "config" / "strategies" / f"{ref}.yaml").read_text("utf-8"))
    manifest = StrategyManifest.model_validate(document)
    builder = REGISTRY[manifest.strategy_id]
    values = dict(document["parameters"])
    values["first_target_rr"] = first
    values["final_target_rr"] = final
    values["stop_atr_multiplier"] = stop

    from tradingagent.research.campaign import CandidateSpec

    return CandidateSpec(
        label=f"tp{first}/tp{final}/stop{stop}",
        manifest=manifest,
        factory=lambda overrides: builder(builder.parameters_model(**overrides)),
        parameters={key: value for key, value in values.items() if isinstance(value, int | float)},
    )


def main(argv: list[str] | None = None) -> int:
    stats = _load_current_stats()
    stats._use_utf8_when_redirected()
    from dataclasses import replace

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="vwap_pullback@1.1.0")
    parser.add_argument("--datasets", type=Path, default=DATASET_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--bars", type=int, default=0, help="tronquer (verification rapide)")
    args = parser.parse_args(argv)

    datasets = stats.DatasetStore(args.datasets).load_all()
    if not datasets:
        raise SystemExit(f"aucun jeu gele dans {args.datasets}")
    markets = sorted(datasets)
    if args.bars > 0:
        datasets = {
            market: replace(
                dataset,
                dataset_id=f"{dataset.dataset_id}-tronque-{args.bars}",
                candles=dataset.candles[-args.bars :],
            )
            for market, dataset in datasets.items()
        }

    def config_for(market: str, dataset: Any) -> Any:
        return replace(stats.config_for(market, dataset), targets_at_first_only=True)

    rows: list[dict[str, Any]] = []
    for first, final, stop in GEOMETRIES:
        label = f"tp{first}/tp{final}/stop{stop}"
        print(f"\n########## {label} ##########")
        for market in markets:
            report = stats.run_campaign(
                {market: datasets[market]},
                [_variant(args.ref, first, final, stop)],
                config_for=config_for,
            )
            measured = report.markets[0]
            candidate = next(
                item
                for item in measured.candidates
                if item.label == f"tp{first}/tp{final}/stop{stop}"
            )
            rows.append(
                {
                    "geometry": label,
                    "first_target_rr": first,
                    "final_target_rr": final,
                    "stop_atr_multiplier": stop,
                    "market": market,
                    "train_trades": candidate.train.trades,
                    "train_pf": stats.number(candidate.train.profit_factor),
                    "train_net": stats.number(candidate.train.net_profit),
                    "validation_trades": candidate.validation.trades,
                    "validation_pf": stats.number(candidate.validation.profit_factor),
                    "validation_net": stats.number(candidate.validation.net_profit),
                }
            )
            print(
                f"  {market:8} train {candidate.train.trades:4} op."
                f" PF {candidate.train.profit_factor:.4f}"
                f" | validation {candidate.validation.trades:4} op. PF "
                f"{candidate.validation.profit_factor:.4f}"
            )

    # -- La regle de decision, appliquee telle qu'elle a ete ecrite en tete de fichier. -------
    by_geometry: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_geometry.setdefault(row["geometry"], {})[row["market"]] = row

    def mean_train_pf(geometry: str) -> float:
        values = [row["train_pf"] for row in by_geometry[geometry].values()]
        return sum(values) / len(values)

    ranked = sorted(by_geometry, key=mean_train_pf, reverse=True)
    chosen = ranked[0]
    worst_validation = min(row["validation_pf"] for row in by_geometry[chosen].values())

    print("\n########## decision ##########")
    print("  classement sur l'entrainement seul (PF net moyen) :")
    for geometry in ranked:
        print(f"    {geometry:<24} entrainement {mean_train_pf(geometry):.4f}")
    print(f"  retenue sur l'entrainement : {chosen}")
    print(
        f"  validation la plus faible des deux marches : {worst_validation:.4f}"
        f"  (plancher {ADOPTION_FLOOR:.2f})"
    )
    adopted = worst_validation >= ADOPTION_FLOOR
    print(f"  VERDICT : {'ADOPTEE' if adopted else 'REFUSEE'}")
    if not adopted:
        print("  Aucune geometrie n'atteint le plancher sur les deux marches :")
        print("  la geometrie d'origine reste, et la cause est ailleurs.")

    payload = {
        "ref": args.ref,
        "bars": args.bars or None,
        "adoption_floor": ADOPTION_FLOOR,
        "selection": "meilleur PF net en entrainement, moyenne sur les deux marches",
        "rows": rows,
        "ranking_train_pf": {geometry: mean_train_pf(geometry) for geometry in ranked},
        "chosen_on_training": chosen,
        "worst_validation_pf": worst_validation,
        "adopted": adopted,
    }
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        path = args.output / f"exit-geometry-{args.ref}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"ecrit : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
