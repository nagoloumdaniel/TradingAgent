"""Equivalence des sources : la copie figee et `src/` donnent-elles le meme resultat ?

C'est le controle qui rend les mesures de cet axe lisibles. La copie figee au commit `bfa0e65`
a servi pendant que l'axe B editait `vwap_pullback.py` ; depuis, le fichier a ete committe
(`fa76d2a`) avec deux filtres optionnels **desactives par defaut**. Si les deux sources donnent
le meme resultat, les mesures faites sur la copie figee restent valables pour la regle en place.
Si elles divergent, elles ne valent que pour la revision gelee, et il faut le dire.

Le controle tourne sur le **jeu entier** (59 999 barres), avec la configuration de reference de
l'axe : stop 1,5 / TP 1,5-3,0, partiel 50/50 + break-even.

    uv run python scripts/backtest/tune_stop_debug.py
"""

import sys
from decimal import Decimal
from pathlib import Path

from tradingagent.backtest.costs import CostModel
from tradingagent.backtest.datasets import DatasetStore

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "backtest"))

from tune_stop_sweep import (
    SYMBOL,
    TIMEFRAME,
    load_strategy_module,
    run_arm,
    stop_grid,
)

BARS = 59999
#: La configuration de reference de l'axe, celle dont le lead a publie le PF sur le jeu entier.
REFERENCE_LABEL = "stop1.5_tp1.5-3.0_partiel"


def measure_with(
    label: str, module: object, candles: tuple, costs: CostModel, reference: str
) -> dict:
    candidate = next(a for a in stop_grid(partial=True) if a.label == reference)
    row = run_arm(candidate, candles, module, costs)
    print(f"\n-- {label} ({reference})")
    print(
        f"   trades {row.get('trades')} reussite {row.get('win_rate')} "
        f"net {row.get('net_eur')} PF {row.get('profit_factor')} "
        f"erreurs {len(row.get('strategy_errors') or [])}"
    )
    return row


def main() -> int:
    datasets = DatasetStore(Path("docs/research/datasets-volume")).load_all()
    dataset = datasets[SYMBOL]
    candles = dataset.candles[-BARS:]
    price = candles[0].close
    costs = CostModel(
        spread=round(price * 5e-5, 6),
        slippage_fixed=round(price * 2e-5, 6),
        commission_per_trade=Decimal("0.5"),
    )
    print(f"{SYMBOL} {TIMEFRAME.value} : {len(candles)} barres ; prix {price}")

    pinned = measure_with(
        "regle figee bfa0e65", load_strategy_module(), candles, costs, REFERENCE_LABEL
    )
    live = measure_with(
        "source vivante src/", load_strategy_module(live=True), candles, costs, REFERENCE_LABEL
    )

    keys = ("trades", "wins", "losses", "net_eur", "profit_factor", "signals", "entries")
    differences = {
        key: (pinned.get(key), live.get(key)) for key in keys if pinned.get(key) != live.get(key)
    }
    print("\n== comparaison champ par champ ==")
    for key in keys:
        mark = "egal" if pinned.get(key) == live.get(key) else "DIFFERENT"
        print(f"   {key:<16} {pinned.get(key)} / {live.get(key)}  {mark}")
    print(f"\n== sources identiques sur ces mesures : {not differences} ==")
    return 0 if not differences else 1


if __name__ == "__main__":
    raise SystemExit(main())
