"""Comparaison des modes de sortie : le partiel + break-even vaut-il mieux que la sortie unique ?

La question a recu deux reponses opposees selon la fenetre, et c'est le resultat le plus utile de
cet axe sur la methode : sur 4 999 bougies la sortie unique gagne la majorite des paires, sur le
jeu complet elle perd les deux paires ou la question se decide. Ce script produit le compte
exact, appaire par appaire, a partir des JSONL de mesure.

    uv run python scripts/backtest/tune_stop_analysis.py --modes
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "docs" / "research" / "vwap-tuning"


def load(name: str) -> list[dict[str, Any]]:
    path = OUTPUT_DIR / name
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def compare(rows: list[dict[str, Any]], title: str) -> None:
    """Apparie partiel 50/50 et sortie unique a stop, TP et gestion identiques."""
    indexed: dict[tuple, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        if row.get("status") != "ok" or row.get("profit_factor") is None:
            continue
        harness = row["harness"]
        if harness["trailing_stop_swing_strength"] or harness["max_holding_bars"]:
            continue
        key = (
            row["parameters"]["stop_atr_multiplier"],
            row["parameters"]["first_target_rr"],
            row["parameters"]["final_target_rr"],
        )
        mode = "partiel" if harness["partial_exit_fractions"] else "unique"
        indexed[key][mode] = row

    pairs = {key: value for key, value in indexed.items() if len(value) == 2}
    print(f"\n=== {title} : {len(pairs)} paire(s) appariee(s) ===")
    print(
        f"  {'stop':>5} {'TP1':>5} {'TP2':>5} | {'partiel':>8} {'unique':>8} "
        f"| {'ecart':>7} | gagnant"
    )
    unique_wins = partial_wins = 0
    for key in sorted(pairs):
        partial = pairs[key]["partiel"]
        single = pairs[key]["unique"]
        gap = partial["profit_factor"] - single["profit_factor"]
        winner = "partiel" if gap > 0 else "unique"
        unique_wins += winner == "unique"
        partial_wins += winner == "partiel"
        print(f"  {key[0]:>5} {key[1]:>5} {key[2]:>5} | {partial['profit_factor']:>8} "
              f"{single['profit_factor']:>8} | {gap:>+7.4f} | {winner}")
    total = unique_wins + partial_wins
    if total:
        print(f"  -> sortie unique {unique_wins}/{total} ; partiel {partial_wins}/{total}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and not stream.isatty():
            reconfigure(encoding="utf-8", errors="replace")
    compare(load("axe-A-exploration-5k.jsonl"), "Exploration, 4 999 bougies")
    compare(
        load("axe-A-decision-60k.jsonl") + load("axe-A-decision-60k-b.jsonl"),
        "Decision, 59 999 bougies",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
