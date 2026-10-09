"""Le test de confirmation : une hypothèse gelée, appliquée une fois, à un autre découpage.

**Pourquoi ce script est écrit ainsi.** Après la campagne du 2026-10-09, un candidat
(`breakout_only:20` sur BTCUSD, `ema_fast=25, ema_slow=50, tp=1.7`) a franchi les neuf portes
avant d'être refusé par la correction de sélection multiple : p-value de 0,3467 pour un seuil
de 0,0019. Le refus était correct — sur 54 réglages essayés, un survivant par hasard est banal.

La seule façon honnête de poursuivre n'est donc **pas** de relancer une recherche sur un autre
jeu : cela créerait 54 nouvelles hypothèses et aggraverait exactement le problème qu'on vient de
mesurer. C'est de **geler** les paramètres, de les appliquer **une fois** à un découpage
différent, et de regarder.

**Pourquoi un autre découpage est un vrai test.** Les jeux H1 sont ré-agrégés depuis les mêmes
bougies M15 : ce ne sont pas des données neuves. Mais `ema_slow=50` couvre 12,5 heures en M15
et **50 heures** en H1. Un edge qui ne survit pas à ce changement de découpage était un artefact
de la résolution choisie, pas une propriété du marché. C'est le seul test orthogonal qui reste
quand le scellé d'un marché a été lu trois fois.

**Aucune recherche, aucune grille, aucun ajustement.** Si le résultat déçoit, on ne réessaie
pas un autre jeu : c'est la règle qui rend ce test valable.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tradingagent.analytics.performance import compute_performance
from tradingagent.backtest.costs import CostModel
from tradingagent.backtest.datasets import CandleDataset, DatasetStore
from tradingagent.backtest.harness import BacktestConfig, run_backtest
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.discovery import BreakoutOnly
from tradingagent.strategies.library.witness import Witness, WitnessParameters
from tradingagent.strategies.manifest import StrategyManifest

#: Les paramètres GELÉS du candidat survivant. Pas ajustés ici, pas même d'un pas.
FROZEN: dict[str, float] = {
    "ema_fast": 25,
    "ema_slow": 50,
    "atr_period": 14,
    "stop_atr_multiplier": 1.5,
    "take_profit_rr": 1.7,
    "entry_zone_atr": 0.1,
}

SETS: dict[str, str] = {
    "M15 (référence, où le candidat a été trouvé)": "docs/research/datasets-long",
    "H1 (nouveau découpage, test de confirmation)": "docs/research/datasets-h1/long-60000/H1",
    "H4 (découpage encore plus grossier)": "docs/research/datasets-h1/long-60000/H4",
}

RISK_EUR = Decimal("10")
COMMISSION_EUR = Decimal("0.5")


def _manifest(dataset: CandleDataset, parameters: WitnessParameters) -> StrategyManifest:
    """L'historique doit suivre le découpage : une barre H1 vaut quatre barres M15."""
    per_bar = {
        Timeframe.M15: 1,
        Timeframe.H1: 6,
        Timeframe.H4: 12,
    }.get(dataset.timeframe, 4)
    lookback = max(parameters.ema_slow, parameters.atr_period) + 2
    return StrategyManifest(
        strategy_id="breakout_only",
        version="0.1.0",
        max_mode=TradingMode.SIGNAL,
        allowed_symbols=(dataset.symbol,),
        timeframes=(dataset.timeframe,),
        history_bars=max(300, lookback * per_bar),
        expiry_bars=2,
        parameters=dict(FROZEN),
    )


def measure(root: Path, symbol: str) -> dict[str, float]:
    datasets = DatasetStore(root).load_all()
    if symbol not in datasets:
        raise SystemExit(f"{symbol} absent de {root}")
    dataset = datasets[symbol]
    price = dataset.candles[0].close
    # Mêmes hypothèses de coûts que la campagne : une fraction du prix, et une commission
    # FIXE. Ce dernier point est un facteur confondant entre découpages, et il faut le dire :
    # le stop vaut quatre fois plus de points en H1, donc les mêmes 0,50 € pèsent quatre fois
    # moins sur le prix payé. Une amélioration en H1 peut donc venir des frais, pas du signal.
    costs = CostModel(
        spread=round(price * 5e-5, 6),
        slippage_fixed=round(price * 2e-5, 6),
        commission_per_trade=COMMISSION_EUR,
    )
    parameters = WitnessParameters.model_validate(FROZEN)
    config = BacktestConfig(
        symbol=symbol,
        costs=costs,
        mode=TradingMode.SIGNAL,
        max_concurrent_positions=1,
        risk_eur=RISK_EUR,
    )
    result = run_backtest(
        BreakoutOnly(parameters, Witness(parameters)),
        _manifest(dataset, parameters),
        {dataset.timeframe: dataset.candles},
        config,
    )
    trades = list(result.trades)
    performance = compute_performance(trades)
    mae = [float(t.mae_r) for t in trades if t.mae_r is not None]
    return {
        "trades": float(performance.trades),
        "win_rate": performance.win_rate or 0.0,
        "net": float(sum((t.pnl_eur for t in trades), Decimal(0))),
        "profit_factor": float(performance.profit_factor or 0.0),
        "mae": (sum(mae) / len(mae)) if mae else 0.0,
    }


def main() -> None:
    print("Test de confirmation — paramètres GELÉS, une seule application par découpage")
    print(f"  {FROZEN}\n")
    header = (
        f"  {'découpage':46} {'marché':8} {'ops':>5} {'réussite':>9} "
        f"{'net EUR':>10} {'PF':>7} {'MAE':>6}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for label, folder in SETS.items():
        root = Path(folder)
        for symbol in ("BTCUSD", "XAUUSD"):
            if not root.exists():
                print(f"  {label:46} {symbol:8} jeu absent")
                continue
            stats = measure(root, symbol)
            print(
                f"  {label:46} {symbol:8} {stats['trades']:>5.0f} {stats['win_rate']:>8.1%} "
                f"{stats['net']:>10.2f} {stats['profit_factor']:>7.3f} {stats['mae']:>6.2f}"
            )


if __name__ == "__main__":
    main()
