"""Soumet une version de manifeste au protocole complet des neuf portes, avec les seuils figes.

**Pourquoi ce script existe.** `run_campaign.py` mesure des *candidats synthetiques* construits
dans le script ; `measure_strategy.py` mesure la version que l'agent execute. Manquait le cas qui
decide d'une adoption : une version **candidate**, ecrite comme un manifeste de production,
passee devant les neuf portes du §49 avec les seuils **figes et verifies** de
`docs/research/thresholds.json`.

**Les seuils sont lus, pas redefinis.** `load_thresholds` refuse un fichier dont le condensat a
bouge depuis sa publication, et ce script ne prend donc pas d'option pour les assouplir. Un
candidat qui a besoin de seuils plus doux n'a pas besoin d'un script, il a besoin d'un meilleur
resultat.

**La sortie est celle de la production.** `targets_at_first_only` est actif parce que
`runtime/pipeline.py` n'envoie que `take_profits[0]` au courtier : mesurer un candidat sur son
second objectif chiffrerait une sortie qu'aucun compte ne prend (defaut corrige le 2026-10-10,
verrouille par `tests/backtest/test_production_exit_parity.py`).

**Le jeu scelle.** Il n'est ouvert que si `--confirm-holdout` est passe, et il ne se rouvre pas :
c'est une ressource qui se depense une fois. Sans le drapeau, la porte `out_of_sample` est
rapportee « non evaluee », et un candidat ne peut alors pas etre retenu sur les neuf.

    uv run python scripts/backtest/validate_candidate.py --ref vwap_pullback@1.3.0 --market BTCUSD
    uv run python scripts/backtest/validate_candidate.py --ref vwap_pullback@1.3.0 \\
        --market BTCUSD --confirm-holdout

Lecture seule sur la production : rien n'est promu, rien n'est ecrit dans `config/`.
"""

import argparse
import importlib.util
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "docs" / "research" / "datasets-volume"
OUTPUT_DIR = ROOT / "docs" / "research" / "validation"
THRESHOLDS = ROOT / "docs" / "research" / "thresholds.json"


def _by_path(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - a packaging accident
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    campaign = _by_path("run_campaign", Path(__file__).with_name("run_campaign.py"))
    stats = _by_path("current_stats", Path(__file__).with_name("current_stats.py"))
    stats._use_utf8_when_redirected()

    from tradingagent.research.promotion import load_thresholds

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True, help="ex. vwap_pullback@1.3.0")
    parser.add_argument("--market", required=True, help="ex. BTCUSD")
    parser.add_argument("--datasets", type=Path, default=DATASET_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--thresholds", type=Path, default=THRESHOLDS)
    parser.add_argument(
        "--head",
        type=int,
        default=0,
        help="ne garder que les N PREMIERES bougies : verification de plomberie, pas une mesure",
    )
    parser.add_argument(
        "--confirm-holdout",
        action="store_true",
        help="ouvre le jeu scelle — ressource non renouvelable, a ne faire qu'une fois",
    )
    args = parser.parse_args(argv)

    thresholds = load_thresholds(args.thresholds)
    print(f"seuils : {args.thresholds.name} version {thresholds.version} (condensat verifie)")
    print(
        f"  PF net minimum {thresholds.min_profit_factor_net} | "
        f"operations minimum {thresholds.min_trades}"
    )

    manifest_path = stats.STRATEGY_DIR / f"{args.ref}.yaml"
    if not manifest_path.exists():
        raise SystemExit(f"{args.ref}: aucun manifeste dans {stats.STRATEGY_DIR}")
    declared = stats.read_manifest(manifest_path)["allowed_symbols"]
    if args.market not in declared:
        raise SystemExit(
            f"{args.market}: {args.ref} ne declare pas ce marche (allowed_symbols={declared})"
        )

    datasets = stats.DatasetStore(args.datasets).load_all()
    if args.market not in datasets:
        raise SystemExit(
            f"{args.market}: aucun jeu gele (disponibles : {', '.join(sorted(datasets))})"
        )
    dataset = datasets[args.market]
    if args.head > 0:
        # Truncated and named as such: the fingerprint follows the candles, so nothing here can
        # be mistaken for the frozen series it came from.
        dataset = replace(
            dataset,
            dataset_id=f"{dataset.dataset_id}-tete-{args.head}",
            candles=dataset.candles[: args.head],
        )
        print(
            f"ATTENTION : {args.market} tronque aux {args.head} PREMIERES bougies — "
            "verification de plomberie, pas une mesure"
        )

    def config_for(market: str, frozen: Any) -> Any:
        return replace(stats.config_for(market, frozen), targets_at_first_only=True)

    print(
        f"candidat : {args.ref} sur {args.market}, jeu {dataset.dataset_id} "
        f"({len(dataset.candles)} bougies)"
    )
    print("sortie mesuree : premier objectif seulement (fidelite production)")
    print(f"jeu scelle : {'OUVERT' if args.confirm_holdout else 'ferme'}")

    report = campaign.run_campaign(
        {args.market: dataset},
        [stats.spec_for(args.market, args.ref)],
        config_for=config_for,
        thresholds=thresholds,
        false_discovery_rate=campaign.DEFAULT_FALSE_DISCOVERY_RATE,
        monte_carlo_iterations=campaign.MONTE_CARLO_ITERATIONS,
        confirm_holdout=args.confirm_holdout,
        anchor=campaign.ANCHOR_SPLIT,
        train_fraction=campaign.TRAIN_FRACTION,
        validation_fraction=campaign.VALIDATION_FRACTION,
    )
    campaign.print_report(report, {args.market: dataset})
    campaign.print_windows(report)
    campaign.print_gates(report)

    args.output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    path = args.output / f"{stamp}-{args.market}-{args.ref}.json"
    path.write_text(
        json.dumps(
            campaign.campaign_to_dict(report, {args.market: dataset}, thresholds),
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"rapport ecrit : {path}")

    candidate = report.markets[0].candidates[0]
    gates = candidate.gates
    verdict = "RETENU" if candidate.selected and not candidate.fragile else "NON RETENU"
    print(f"\nVERDICT : {verdict}")
    print(f"  retenu par la selection : {candidate.selected}")
    print(f"  fragile                 : {candidate.fragile}")
    if candidate.reasons:
        print(f"  fragilites : {' ; '.join(candidate.reasons)}")
    if gates is not None:
        print(f"  p-value                 : {gates.p_value}")
        print(
            f"  plis rentables          : {gates.walk_forward.profitable_folds}"
            f"/{gates.walk_forward.folds}"
        )
    if report.multiple_testing is not None:
        multiple = report.multiple_testing
        print(f"  hypothese(s) pricee(s)  : {multiple.hypotheses} a alpha={multiple.alpha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
