"""Agrege les releves de spread et dit ce qu'ils permettent de conclure, et rien de plus.

**Pourquoi un second script.** `collect_spread.py` enregistre des points et rend un tableau
preliminaire sur son propre releve. Celui-ci relit **tous** les releves accumules, les joint, et
surtout applique la seule discipline qui compte ici : **ne rien conclure d'une heure qui n'a pas
assez de points**, et ecrire noir sur blanc ce que la couverture ne permet pas de dire.

**Ce que le spread doit etre compare a.** Pas au dollar : a l'ATR M15. C'est ce rapport-la qui
decide si une porte d'entree du depot est calibree, parce que le risque d'une operation s'exprime
en ATR. Le 2026-10-10, un releve court a donne 18,80 $ pour le BTC, soit **0,306 ATR M15**, contre
0,740 $ pour l'or, soit **0,185 ATR** : le bitcoin coute donc environ **1,65 fois plus cher que
l'or en proportion du mouvement qu'il offre**. C'est un fait de structure, pas de reglage.

    uv run python scripts/analysis/aggregate_spread.py
    uv run python scripts/analysis/aggregate_spread.py --min-per-hour 20
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SPREAD_DIR = ROOT / "docs" / "research" / "spread"
OUTPUT_DIR = ROOT / "docs" / "research" / "spread"


def load_samples(directory: Path) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for path in sorted(directory.glob("spread-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                samples.append(json.loads(line))
    return samples


def _median(values: list[float]) -> float:
    return statistics.median(values)


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction)))
    return ordered[index]


def report_symbol(symbol: str, rows: list[dict[str, Any]], min_per_hour: int) -> dict[str, Any]:
    spreads = [float(row["spread"]) for row in rows]
    ratios = [float(row["spread_over_atr"]) for row in rows if row.get("spread_over_atr")]
    by_hour: dict[int, list[float]] = defaultdict(list)
    by_weekday: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        at = datetime.fromisoformat(str(row["at"]))
        by_hour[at.hour].append(float(row["spread"]))
        by_weekday[at.weekday()].append(float(row["spread"]))

    print(f"\n== {symbol} : {len(rows)} releve(s) ==")
    print(
        f"  spread   median {_median(spreads):.4f}   p05 {_quantile(spreads, 0.05):.4f}"
        f"   p95 {_quantile(spreads, 0.95):.4f}"
        f"   min {min(spreads):.4f}  max {max(spreads):.4f}"
    )
    if ratios:
        print(f"  spread/ATR M15  median {_median(ratios):.4f}   p95 {_quantile(ratios, 0.95):.4f}")

    print(f"  par heure UTC (concluantes seulement au-dela de {min_per_hour} points) :")
    hourly: dict[str, Any] = {}
    for hour in range(24):
        values = by_hour.get(hour, [])
        if not values:
            continue
        enough = len(values) >= min_per_hour
        hourly[f"{hour:02d}"] = {
            "samples": len(values),
            "median": round(_median(values), 4),
            "conclusive": enough,
        }
        mark = "" if enough else "   (trop peu de points — non concluant)"
        print(f"    {hour:02d}h  n={len(values):4}  median {_median(values):>10.4f}{mark}")

    print("  par jour de semaine :")
    names = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
    weekly: dict[str, Any] = {}
    for day in range(7):
        values = by_weekday.get(day, [])
        if not values:
            continue
        enough = len(values) >= min_per_hour
        weekly[names[day]] = {
            "samples": len(values),
            "median": round(_median(values), 4),
            "conclusive": enough,
        }
        mark = "" if enough else "   (trop peu de points — non concluant)"
        print(f"    {names[day]:<9} n={len(values):4}  median {_median(values):>10.4f}{mark}")

    return {
        "symbol": symbol,
        "samples": len(rows),
        "spread_median": round(_median(spreads), 4),
        "spread_p05": round(_quantile(spreads, 0.05), 4),
        "spread_p95": round(_quantile(spreads, 0.95), 4),
        "spread_min": round(min(spreads), 4),
        "spread_max": round(max(spreads), 4),
        "spread_over_atr_median": round(_median(ratios), 4) if ratios else None,
        "spread_over_atr_p95": round(_quantile(ratios, 0.95), 4) if ratios else None,
        "by_hour": hourly,
        "by_weekday": weekly,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=SPREAD_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument(
        "--min-per-hour",
        type=int,
        default=10,
        help="points minimum pour qu'une heure soit concluante",
    )
    args = parser.parse_args(argv)

    samples = load_samples(args.directory)
    if not samples:
        raise SystemExit(f"aucun releve dans {args.directory}")

    stamps = sorted(datetime.fromisoformat(str(row["at"])) for row in samples)
    span = stamps[-1] - stamps[0]
    print(f"{len(samples)} releve(s) au total")
    print(
        f"du {stamps[0].isoformat()} au {stamps[-1].isoformat()}"
        f"  ({span.total_seconds() / 3600:.2f} h)"
    )
    print(f"jours couverts : {len({stamp.date() for stamp in stamps})}")

    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in samples:
        by_symbol[str(row["symbol"])].append(row)

    blocks = [
        report_symbol(symbol, rows, args.min_per_hour) for symbol, rows in sorted(by_symbol.items())
    ]

    payload = {
        "samples": len(samples),
        "first": stamps[0].isoformat(),
        "last": stamps[-1].isoformat(),
        "span_hours": round(span.total_seconds() / 3600, 2),
        "min_samples_per_hour": args.min_per_hour,
        "symbols": blocks,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "spread-summary.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\necrit : {path}")

    conclusive = [
        (block["symbol"], hour, figures)
        for block in blocks
        for hour, figures in block["by_hour"].items()
        if figures["conclusive"]
    ]
    print(
        f"\nheures concluantes : {len(conclusive)} sur "
        f"{sum(len(block['by_hour']) for block in blocks)} heures couvertes"
    )
    if len(conclusive) < 4:
        print(
            "MOINS DE QUATRE HEURES CONCLUANTES : ce releve ne permet PAS de trancher si le "
            "spread depend de l'heure. Un tableau horaire sur deux ou trois points est du bruit, "
            "et la question du filtre horaire reste ouverte."
        )
    print(
        "\nCe que ce releve ne dit pas, et qu'aucune ligne ci-dessus ne doit faire oublier :\n"
        "  * rien sur les publications macro, qui sont les pics de spread du BTC ;\n"
        "  * rien sur la stabilite du courtier dans le temps — un releve est un instantane ;\n"
        "  * rien sur l'impact, le slippage ou les requotes, qui ne se lisent pas dans un tick."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
