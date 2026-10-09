"""Ce que les mesures de l'axe A ne disent pas d'elles-memes.

Trois verifications, et chacune peut retirer de la force a une conclusion :

1. **L'invariant annonce tient-il ?** Le balayage est presente comme « meme jeu de signaux,
   seule la geometrie change ». La detection ne depend que de `pullback_atr`, `entry_zone_atr`,
   des EMA et de la pente — tous fixes. Mais c'est une affirmation sur le code, pas sur les
   mesures : ce script la verifie sur les compteurs reellement produits (`signals`).
2. **Le confondant des places occupees.** `max_concurrent_positions=1` : un signal n'est
   execute que si aucune position n'est ouverte. Or un stop plus serre libere la place plus
   vite, donc le nombre de trades **executes** change avec le stop, meme a jeu de signaux
   constant. Une comparaison de PF entre stops compare donc aussi deux echantillons de trades
   differents. C'est un fait a ecrire, pas a taire.
3. **Le prix de reference des couts**, par fenetre : le modele de couts est proportionnel au
   close de la premiere bougie de la fenetre mesuree, et deux fenetres n'ont donc pas les memes
   couts absolus.

    uv run python scripts/backtest/tune_stop_analysis.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "docs" / "research" / "vwap-tuning"
DATASETS = ROOT / "docs" / "research" / "datasets-volume"


def load(name: str) -> list[dict[str, Any]]:
    path = OUTPUT_DIR / name
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def signal_invariance(rows: list[dict[str, Any]], title: str) -> None:
    """Le compteur de signaux depend-il du stop, a paire de TP et mode de sortie egaux ?"""
    print(f"\n=== invariance du jeu de signaux — {title} ===")
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        parameters = row["parameters"]
        key = (
            parameters["first_target_rr"],
            parameters["final_target_rr"],
            tuple(row["harness"]["partial_exit_fractions"]),
            row["harness"]["trailing_stop_swing_strength"],
            row["harness"]["max_holding_bars"],
        )
        groups[key].append(row)
    unstable = 0
    for key, members in sorted(groups.items(), key=lambda item: str(item[0])):
        counts = {member["signals"] for member in members}
        stops = sorted(member["parameters"]["stop_atr_multiplier"] for member in members)
        state = "stable" if len(counts) == 1 else "INSTABLE"
        if len(counts) != 1:
            unstable += 1
        print(
            f"  {state:<8} TP {key[0]}/{key[1]} partiel {key[2]} "
            f"swing {key[3]} holding {key[4]} : signaux {sorted(counts)} sur stops {stops}"
        )
    print(f"  -> {unstable} groupe(s) instable(s) sur {len(groups)}")


def slot_confound(rows: list[dict[str, Any]], title: str) -> None:
    """Combien de signaux sont perdus faute de place, et comment cela varie avec le stop."""
    print(f"\n=== confondant des places occupees — {title} ===")
    print(f"  {'configuration':<40} {'signaux':>8} {'entrees':>8} {'sans place':>11} {'trades':>7}")
    for row in sorted(rows, key=lambda r: (r["parameters"]["stop_atr_multiplier"], r["label"])):
        if row.get("status") != "ok":
            continue
        print(
            f"  {row['label']:<40} {row['signals']:>8} {row['entries']:>8} "
            f"{row['skipped_no_room']:>11} {row['trades']:>7}"
        )


def stopped_share(rows: list[dict[str, Any]], title: str) -> None:
    """Part des trades qui meurent au stop, deduite de la MAE : 1 R ou plus, c'est le stop.

    Les gagnants sont exclus du calcul : un gagnant dont la MAE depasse 1 R est impossible avec
    un stop a 1 R, sauf fill adverse. La statistique porte donc sur les perdants.
    """
    print(f"\n=== excursion adverse — {title} ===")
    for row in sorted(rows, key=lambda r: r["label"]):
        if row.get("status") != "ok" or not row.get("trades"):
            continue
        print(
            f"  {row['label']:<40} perdants MAE mediane {row.get('losers_mae_r_median')} "
            f"| gagnants MFE mediane {row.get('winners_mfe_r_median')} "
            f"| reussite {row.get('win_rate')}"
        )


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and not stream.isatty():
            reconfigure(encoding="utf-8", errors="replace")

    exploration = load("axe-A-exploration-5k.jsonl")
    decision_a = load("axe-A-decision-60k.jsonl")
    decision_b = load("axe-A-decision-60k-b.jsonl")

    if exploration:
        signal_invariance(exploration, "exploration 4 999 barres")
        slot_confound(exploration[:0] or exploration, "exploration 4 999 barres (extrait)")
    if decision_a or decision_b:
        signal_invariance(decision_a + decision_b, "decision 59 999 barres")
        stopped_share(decision_a + decision_b, "decision 59 999 barres")

    print("\n=== prix de reference des couts, par fenetre ===")
    from tradingagent.backtest.datasets import DatasetStore

    dataset = DatasetStore(DATASETS).load_all()["BTCUSD"]
    for label, candles in (
        ("jeu complet 59 999", dataset.candles[-59999:]),
        ("derniere moitie", dataset.candles[30000:59999]),
        ("premiere moitie", dataset.candles[0:30000]),
        ("5 000 dernieres", dataset.candles[-4999:]),
        ("20 000 premieres", dataset.candles[:20000]),
        ("20 000 dernieres", dataset.candles[-20000:]),
    ):
        price = candles[0].close
        print(
            f"  {label:<20} {len(candles):>6} barres du {candles[0].open_time.date()} "
            f"au {candles[-1].open_time.date()} ; close initial {price} ; "
            f"spread {round(price * 5e-5, 6)} ; slippage {round(price * 2e-5, 6)}"
        )

    measured = [r for r in decision_a + decision_b if r.get("status") == "ok"]
    if measured:
        best = max(measured, key=lambda r: r["profit_factor"] or 0)
        above = [r for r in measured if (r["profit_factor"] or 0) > 1.0]
        print("\n=== bilan des mesures de decision ===")
        print(f"  {len(measured)} configuration(s) mesuree(s) sur le jeu complet")
        print(f"  meilleur PF {best['profit_factor']} ({best['label']})")
        print(
            f"  configurations au-dessus de PF 1,0 : {len(above)}"
            + (f" -> {[r['label'] for r in above]}" if above else "")
        )
        print(
            f"  realisees gagnantes : {[r['label'] for r in measured if (r['net_eur'] or 0) > 0]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["load", "main", "signal_invariance", "slot_confound", "stopped_share"]
