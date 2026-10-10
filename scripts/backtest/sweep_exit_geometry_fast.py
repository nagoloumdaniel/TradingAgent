"""Le meme balayage de geometrie, sans les portes dont la regle de decision ne se sert pas.

**Pourquoi ce fichier existe a cote de `sweep_exit_geometry.py`.** La version complete appelle
`run_campaign`, qui calcule pour chaque candidat les plis walk-forward (235 par marche), le
Monte-Carlo, les variantes de robustesse et les portes du §49. La regle de decision ecrite en
tete de ce balayage ne lit que **deux** nombres : le facteur de profit net en entrainement et en
validation. Tout le reste est calcule puis jete — et c'est ce qui a fait durer la campagne
complete plus de deux heures sans avoir rien produit.

Mesure du 2026-10-10 : lancement a 19:45, toujours en cours a 22:07, 8 435 s de CPU consommees
pour zero resultat ecrit. La cause n'est pas un blocage mais le volume de calcul inutile.

**Ce qui est identique, et c'est ce qui compte.** Le decoupage vient du meme
`split_dataset(anchor=True, 0.6, 0.2)`, les fenetres sont passees telles quelles au meme
`run_backtest`, avec la meme configuration de couts et le meme `targets_at_first_only` — c'est
d'ailleurs exactement ce que fait `campaign._run_candidate` pour ses deux premiers appels.
Seules les portes sont omises.

    uv run python scripts/backtest/sweep_exit_geometry_fast.py
    uv run python scripts/backtest/sweep_exit_geometry_fast.py --bars 20000

Lecture seule : rien n'est promu, rien n'est ecrit dans `config/`, le jeu scelle reste ferme.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure_strategy import _load_current_stats
from sweep_exit_geometry import (
    ADOPTION_FLOOR,
    DATASET_DIR,
    GEOMETRIES,
    ROOT,
    _label,
    _refs_from_agent_config,
    _variant,
)

OUTPUT_DIR = ROOT / "docs" / "research" / "measurements"


def _performance(run: Any, stats: Any) -> dict[str, Any]:
    performance = run.performance
    return {
        "trades": performance.trades,
        "wins": performance.wins,
        "losses": performance.losses,
        "win_rate": stats.number(performance.win_rate),
        "net_profit": stats.number(performance.net_profit),
        "profit_factor": stats.number(performance.profit_factor),
        "expectancy": stats.number(performance.expectancy),
        "max_drawdown": stats.number(performance.max_drawdown),
    }


def _run_spec(spec: Any, dataset: Any, candles: Any, config: Any) -> Any:
    """One window through the campaign's own call: same factory, same manifest, same config."""
    from tradingagent.backtest.harness import run_backtest

    return run_backtest(
        spec.factory(spec.parameters), spec.manifest, {dataset.timeframe: candles}, config
    )


def main(argv: list[str] | None = None) -> int:
    stats = _load_current_stats()
    stats._use_utf8_when_redirected()
    from dataclasses import replace

    from tradingagent.research.protocol import split_dataset

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-config", type=Path, default=ROOT / "config" / "agent.yaml")
    parser.add_argument("--datasets", type=Path, default=DATASET_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--bars", type=int, default=0, help="tronquer (verification rapide)")
    args = parser.parse_args(argv)

    refs = _refs_from_agent_config(args.agent_config)
    print(f"versions declarees : {refs}")

    datasets = stats.DatasetStore(args.datasets).load_all()
    if not datasets:
        raise SystemExit(f"aucun jeu gele dans {args.datasets}")
    markets = sorted(datasets)
    missing = [market for market in markets if market not in refs]
    if missing:
        raise SystemExit(f"aucune version declaree pour : {', '.join(missing)}")
    if args.bars > 0:
        datasets = {
            market: replace(
                dataset,
                dataset_id=f"{dataset.dataset_id}-tronque-{args.bars}",
                candles=dataset.candles[-args.bars :],
            )
            for market, dataset in datasets.items()
        }

    # Le decoupage de la campagne, a l'identique : ancre, 60 % / 20 % / 20 % scelle.
    splits = {}
    for market in markets:
        split = split_dataset(datasets[market], token=f"fast-sweep:{market}", anchor=True)
        splits[market] = split
        train_window, validation_window, sealed = split.windows
        print(
            f"  {market} : entrainement {train_window.bars} barres "
            f"{train_window.start:%Y-%m-%d} -> {train_window.end:%Y-%m-%d} | "
            f"validation {validation_window.bars} barres "
            f"{validation_window.start:%Y-%m-%d} -> {validation_window.end:%Y-%m-%d} | "
            f"scelle {sealed.bars} barres (non ouvert)"
        )

    def config_for(market: str, dataset: Any) -> Any:
        return replace(stats.config_for(market, dataset), targets_at_first_only=True)

    rows: list[dict[str, Any]] = []
    for first, final, stop in GEOMETRIES:
        label = _label(first, final, stop)
        print(f"\n########## {label} ##########")
        for market in markets:
            dataset, split = datasets[market], splits[market]
            spec = _variant(refs[market], first, final, stop)
            base_config = config_for(market, dataset)
            train_run = _run_spec(spec, dataset, split.train, base_config)
            validation_run = _run_spec(spec, dataset, split.validation, base_config)
            rows.append(
                {
                    "geometry": label,
                    "first_target_rr": first,
                    "final_target_rr": final,
                    "stop_atr_multiplier": stop,
                    "market": market,
                    "ref": refs[market],
                    "train": _performance(train_run, stats),
                    "validation": _performance(validation_run, stats),
                }
            )
            print(
                f"  {market:8} train {train_run.performance.trades:4} op."
                f" PF {train_run.performance.profit_factor:.4f}"
                f" | validation {validation_run.performance.trades:4} op. PF "
                f"{validation_run.performance.profit_factor:.4f}"
            )

    by_geometry: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_geometry.setdefault(row["geometry"], {})[row["market"]] = row

    def mean_train_pf(geometry: str) -> float:
        values = [row["train"]["profit_factor"] for row in by_geometry[geometry].values()]
        return sum(values) / len(values)

    ranked = sorted(by_geometry, key=mean_train_pf, reverse=True)
    chosen = ranked[0]
    worst_validation = min(
        row["validation"]["profit_factor"] for row in by_geometry[chosen].values()
    )

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
        "method": "fast: train and validation only, same split and same harness as the campaign",
        "refs": refs,
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
        path = args.output / "exit-geometry-sweep-fast.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"ecrit : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
