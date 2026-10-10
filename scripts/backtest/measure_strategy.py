"""Mesure une version de manifeste sur les deux marches, avec le protocole de campagne complet.

**Pourquoi ce script existe.** `current_stats.py` mesure ce que `config/agent.yaml` declare :
il repond a « ou en est ce qui tourne ? ». La question posee ici est differente — « que vaut
cette version *avant* de la mettre en production ? » — donc la version est passee en argument,
et le jeu de donnees aussi. Sans cela, on ne peut mesurer un candidat qu'en l'activant d'abord,
ce qui est exactement l'ordre que le projet interdit.

Reutilise, sans le recopier : `run_campaign` (protocole train/validation/robustesse, portes,
correction de tests multiples) et les fonctions de lecture de `current_stats.py`.

    uv run python scripts/backtest/measure_strategy.py --ref vwap_pullback@1.1.0
    uv run python scripts/backtest/measure_strategy.py --ref vwap_pullback@1.1.0 --market XAUUSD

Lecture seule : rien n'est promu, rien n'est ecrit dans `config/`, le jeu scelle reste ferme.
"""

import argparse
import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "docs" / "research" / "datasets-volume"
OUTPUT_DIR = ROOT / "docs" / "research" / "measurements"


def _load_current_stats() -> Any:
    """`current_stats.py` as a module, by path: the file name is a script, not a package name."""
    path = Path(__file__).with_name("current_stats.py")
    spec = importlib.util.spec_from_file_location("current_stats", path)
    if spec is None or spec.loader is None:  # pragma: no cover - a packaging accident
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["current_stats"] = module
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    stats = _load_current_stats()
    stats._use_utf8_when_redirected()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True, help="ex. vwap_pullback@1.1.0")
    parser.add_argument("--datasets", type=Path, default=DATASET_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--market", default=None, help="ne mesurer qu'un marche")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument(
        "--all-targets",
        action="store_true",
        help="garder tous les objectifs (mesure non fidele a la production, pour comparaison)",
    )
    args = parser.parse_args(argv)

    def config_for(market: str, dataset: Any) -> Any:
        """The charged cost model, plus the exit production takes.

        `targets_at_first_only` is on because `runtime/pipeline.py` sends `take_profits[0]` and
        nothing under `execution/` closes a fraction of a position. Measuring a two-objective
        rule on its second objective would price an exit that no account ever takes — the
        verified defect of 2026-10-10, pinned by `tests/backtest/test_production_exit_parity.py`.
        """
        base = stats.config_for(market, dataset)
        if args.all_targets:
            return base
        return replace(base, targets_at_first_only=True)

    manifest_path = stats.STRATEGY_DIR / f"{args.ref}.yaml"
    if not manifest_path.exists():
        raise SystemExit(f"{args.ref}: aucun manifeste dans {stats.STRATEGY_DIR}")

    declared_symbols = stats.read_manifest(manifest_path)["allowed_symbols"]
    datasets = stats.DatasetStore(args.datasets).load_all()
    if not datasets:
        raise SystemExit(f"aucun jeu gele dans {args.datasets}")

    markets = [args.market] if args.market else sorted(datasets)
    blocks: list[dict[str, Any]] = []
    for market in markets:
        if market not in datasets:
            raise SystemExit(
                f"{market}: aucun jeu gele (disponibles : {', '.join(sorted(datasets))})"
            )
        if market not in declared_symbols:
            # Explicit rather than silent: the harness would refuse anyway, and a skip that
            # looks like a pass is the failure mode this whole project is built against.
            raise SystemExit(
                f"{market}: {args.ref} ne declare pas ce marche "
                f"(allowed_symbols={declared_symbols})"
            )
        dataset = datasets[market]
        report = stats.run_campaign(
            {market: dataset},
            [stats.spec_for(market, args.ref)],
            config_for=config_for,
        )
        blocks.append(stats.market_block(market, args.ref, dataset, report.markets[0]))

    for block in blocks:
        stats.print_block(block)

    if not args.no_write and blocks:
        args.output.mkdir(parents=True, exist_ok=True)
        for block in blocks:
            path = args.output / f"{block['market']}-{block['ref']}.json"
            path.write_text(
                json.dumps(block, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            print(f"ecrit : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
