"""Résumé lisible d'un JSON de parité : les compteurs de la porte, par base de verdict.

    uv run python docs/research/execution-diagnostic/tools/read_verdicts.py [chemin.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT = Path(__file__).with_name("parity-entry-zone.json")


def _pf(value: float | None) -> str:
    """Le profit factor, ou un tiret quand il n'y a pas de perte pour le calculer."""
    return "—" if value is None else f"{round(value, 4)}"


def line(label: str, summary: dict) -> str:
    return (
        f"  {label:26} {summary['count']:>5} entrées  net {summary['net_eur']:>+10.2f} EUR  "
        f"réussite {summary['win_rate']:>6.1%}  PF {_pf(summary['profit_factor'])}  "
        f"moyenne {summary['average_eur']:>+7.2f}  A/V {summary['by_direction']}"
    )


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    payload = json.loads(path.read_text(encoding="utf-8"))
    print(f"{path.name} — {payload['bars']} bougies, {payload['dataset_id']}")
    for name, stats in payload.get("scenarios", {}).items():
        print(
            f"\nscénario {name} : {stats['trades']} trades, réussite {stats['win_rate']:.1%}, "
            f"net {stats['net_eur']:+.2f} EUR, PF {_pf(stats['profit_factor'])}, "
            f"spread {stats['spread_usd']:.4f} USD, refusés {stats['refused_entry_zone']}, "
            f"sans place {stats['skipped_no_room']}, signaux {stats['signals']}"
        )
        if "reference_check" in payload:
            print(f"  {payload['reference_check']}")
    for key, block in payload.items():
        if not key.startswith("verdicts_"):
            continue
        print(f"\n=== {key} ===")
        print(f"  entrées examinées {block['trades_examined']} "
              f"(non appariées {block['unmatched_signals']}, "
              f"incohérentes {block['reconstruction_mismatches']}); "
              f"bande/spread médian {block['band_over_spread_median']:.2f}x, "
              f"spread {block['spread_usd']:.4f} USD")
        print(line("refusées (paid)", block["refused"]))
        print(line("acceptées (paid)", block["taken"]))
        print(line("ask entier (production)", block["production_basis"]["refused"]))
        print(line("instant du signal", block["structural_rule"]["refused"]))
        print(line("option B (reference)", block["reference_basis"]["refused"]))
        print(f"  dépassement médian des refus : {block['refused_overshoot_usd_median']:+.2f} USD "
              f"(max {block['refused_overshoot_usd_max']:+.2f})")
        print("  courbe de largeur (entrées inchangées) :")
        for width, summary in block["width_curve"].items():
            print(line(f"    {width} ATR", summary))
    for key, block in payload.items():
        if key.endswith("_vs_baseline") or key.endswith("_vs_baseline_prod_spread"):
            print(f"\n{key} : {json.dumps(block, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
