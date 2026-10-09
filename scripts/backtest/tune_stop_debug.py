"""Diagnostic : la regle figee produit-elle bien la reference documentee ?

Verifie deux choses, et rien d'autre :

1. que le module charge depuis `docs/research/vwap-tuning/vwap_pullback_pinned_bfa0e65.py`
   reproduit exactement la mesure documentee (stop 1,5 / TP 1,5-3,0, partiel 50/50 +
   break-even) : 268 trades, net -177,76 EUR, PF 0,9002 sur 19 999 barres ;
2. que la source vivante `src/` s'en ecarte ou non, pour savoir si les mesures de cet axe
   restent valables une fois les autres axes retombes.

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

BARS = 19999


def measure_with(label: str, module: object, candles: tuple, costs: CostModel) -> dict:
    candidate = next(a for a in stop_grid(partial=True) if a.label == "stop1.5_tp1.5-3.0_partiel")
    row = run_arm(candidate, candles, module, costs)
    print(f"\n-- {label}")
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

    pinned = measure_with("regle figee bfa0e65", load_strategy_module(), candles, costs)
    live = measure_with("source vivante src/", load_strategy_module(live=True), candles, costs)

    same = (pinned.get("trades"), pinned.get("net_eur")) == (
        live.get("trades"),
        live.get("net_eur"),
    )
    print(f"\n== identiques : {same} ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
