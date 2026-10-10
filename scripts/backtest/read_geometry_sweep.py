"""Restitue le balayage de geometrie en un tableau lisible, depuis le JSON ecrit par le script."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = json.loads(
    (ROOT / "docs" / "research" / "measurements" / "exit-geometry-sweep-fast.json").read_text(
        "utf-8"
    )
)

rows = PAYLOAD["rows"]
markets = sorted({row["market"] for row in rows})
geometries = list(PAYLOAD["ranking_train_pf"])
chosen = PAYLOAD["chosen_on_training"]

# Par geometrie : entrainement -> validation, marche par marche.
print(f"{'geometrie':<24} {'marche':<8} {'train n':>8} {'train PF':>9} {'val n':>7} {'val PF':>8}")
print("-" * 70)
for geometry in geometries:
    for market in markets:
        row = next(r for r in rows if r["geometry"] == geometry and r["market"] == market)
        print(
            f"{geometry:<24} {market:<8} {row['train']['trades']:>8} "
            f"{row['train']['profit_factor']:>9.4f} {row['validation']['trades']:>7} "
            f"{row['validation']['profit_factor']:>8.4f}"
        )
    print("-" * 70)

print()
print("classement sur l'entrainement seul (PF net moyen des deux marches) :")
for geometry in geometries:
    print(f"  {geometry:<24} {PAYLOAD['ranking_train_pf'][geometry]:.4f}")
print()
print(f"retenue sur l'entrainement : {PAYLOAD['chosen_on_training']}")
print(f"validation la plus faible   : {PAYLOAD['worst_validation_pf']:.4f}")
print(f"plancher d'adoption         : {PAYLOAD['adoption_floor']:.2f}")
print(f"VERDICT                     : {'ADOPTEE' if PAYLOAD['adopted'] else 'REFUSEE'}")
print()
print(f"geometrie retenue sur l'entrainement : {PAYLOAD['chosen_on_training']}")
for market in markets:
    row = next(r for r in rows if r["geometry"] == chosen and r["market"] == market)
    print(
        f"  {market:8} PF validation {row['validation']['profit_factor']:.4f}"
        f" | net {row['validation']['net_profit']:.2f} EUR"
        f" | {row['validation']['trades']} operations"
    )
