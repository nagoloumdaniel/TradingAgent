"""Mesure consolidee : les strategies de production, sur leurs jeux reels, avec et sans couts.

    uv run python scripts/backtest/measure_production.py
    uv run python scripts/backtest/measure_production.py --symbol BTCUSD

Pourquoi ce script : `run_campaign.py` evalue des candidats de recherche contre les 9 portes.
Ici on veut le chiffre brut des strategies **de production** telles que `agent.yaml` les
affecte, sur le jeu gele de leur marche, avec le modele de couts du depot et a couts doubles.
Aucune promotion, aucun manifeste ecrit : c'est une lecture.
"""

import argparse
from decimal import Decimal
from pathlib import Path

from tradingagent.backtest.costs import CostModel
from tradingagent.backtest.datasets import CandleDataset, DatasetStore
from tradingagent.backtest.harness import BacktestConfig, run_backtest
from tradingagent.config.strategy_catalog import load_strategy_catalog
from tradingagent.core.mode import TradingMode
from tradingagent.strategies.registry import REGISTRY

ROOT = Path(__file__).resolve().parents[2]
STRATEGIES = ROOT / "config" / "strategies"

#: Le jeu gele a utiliser par symbole, dans l'ordre de preference : le plus long d'abord.
CANDIDATE_DIRS = (
    "docs/research/datasets-long",
    "docs/research/datasets",
    "docs/research/datasets/M1",
)


def load_catalogue():
    return load_strategy_catalog(STRATEGIES, REGISTRY)


def dataset_for(symbol: str) -> tuple[CandleDataset, Path]:
    """Le jeu gele le plus long disponible pour ce symbole, avec son repertoire."""
    best: tuple[CandleDataset, Path] | None = None
    for relative in CANDIDATE_DIRS:
        directory = ROOT / relative
        if not directory.is_dir():
            continue
        for dataset in DatasetStore(directory).load_all().values():
            if dataset.symbol != symbol:
                continue
            if best is None or dataset.bars > best[0].bars:
                best = (dataset, directory)
    if best is None:
        raise SystemExit(f"aucun jeu gele pour {symbol} dans {CANDIDATE_DIRS}")
    return best


def cost_model(dataset: CandleDataset, multiplier: float = 1.0) -> CostModel:
    """Le modele du depot (`run_campaign.config_for`), au facteur demande."""
    price = dataset.candles[0].close
    return CostModel(
        spread=round(price * 0.00005, 6),
        slippage_fixed=round(price * 0.00002, 6),
        commission_per_trade=Decimal("0.5"),
    ).stressed(multiplier)


def report(label: str, result, *, bars: int) -> dict[str, float]:
    performance = result.performance
    wins = [float(trade.pnl_eur) for trade in result.trades if trade.pnl_eur > 0]
    losses = [-float(trade.pnl_eur) for trade in result.trades if trade.pnl_eur <= 0]
    gross_win, gross_loss = sum(wins), sum(losses)
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    print(f"\n--- {label} ---")
    print(f"  bougies fournies        : {bars}")
    print(f"  operations              : {performance.trades}")
    print(f"  gagnants / perdants     : {performance.wins} / {performance.losses}")
    print(
        f"  taux de reussite        : {float(performance.win_rate):.2%}"
        if performance.win_rate is not None
        else "  taux de reussite        : n/a"
    )
    print(f"  esperance par operation : {performance.expectancy}")
    print(f"  resultat net            : {performance.net_profit} EUR")
    print(f"  profit factor           : {profit_factor:.4f}")
    print(f"  drawdown maximal        : {performance.max_drawdown} EUR")
    if wins:
        print(f"  gain moyen              : {gross_win / len(wins):.4f} EUR")
    if losses:
        print(f"  perte moyenne           : {-gross_loss / len(losses):.4f} EUR")
    print(f"  erreurs de strategie    : {list(result.strategy_errors)[:2] or 'aucune'}")
    return {
        "trades": float(performance.trades),
        "win_rate": float(performance.win_rate or 0),
        "net": float(performance.net_profit),
        "profit_factor": profit_factor,
    }


def measure(ref: str, symbol: str, catalogue) -> None:
    if ref not in catalogue:
        print(f"\n### {ref} : absent du catalogue (config/strategies), ignore")
        return
    loaded = catalogue[ref]
    dataset, directory = dataset_for(symbol)
    print(f"\n{'=' * 78}")
    print(f"### {ref} sur {symbol}  —  manifeste {loaded.manifest.ref}")
    print(f"{'=' * 78}")
    print(f"jeu       : {dataset.dataset_id} ({directory.relative_to(ROOT)})")
    print(f"bougies   : {dataset.bars} {dataset.timeframe.value}")
    print(f"periode   : {dataset.start.isoformat()} -> {dataset.end.isoformat()}")
    print(f"empreinte : {dataset.fingerprint[:24]}...")
    print(
        f"plafond   : max_mode={loaded.manifest.max_mode.value} | "
        f"ai_filter={loaded.manifest.ai_filter.value} | "
        f"UT={[t.value for t in loaded.manifest.timeframes]} | "
        f"history_bars={loaded.manifest.history_bars}"
    )

    series = {dataset.timeframe: dataset.candles}
    for multiplier, label in ((0.0, "SANS COUTS"), (1.0, "COUTS DU DEPOT"), (2.0, "COUTS x2")):
        costs = CostModel() if multiplier == 0.0 else cost_model(dataset, multiplier)
        config = BacktestConfig(
            symbol=symbol,
            costs=costs,
            mode=TradingMode.SIGNAL,
            max_concurrent_positions=1,
        )
        try:
            result = run_backtest(loaded.strategy, loaded.manifest, series, config)
        except ValueError as error:
            print(f"\n--- {label} ---\n  REFUSE : {error}")
            continue
        report(label, result, bars=dataset.bars)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=None, help="limiter a un symbole")
    args = parser.parse_args()

    catalogue = load_catalogue()
    assignments = {
        "XAUUSD": "witness@1.1.1",
        "BTCUSD": "trend_breakout@1.0.1",
    }
    selected = {args.symbol: assignments[args.symbol]} if args.symbol else assignments
    print(f"strategies de production mesurees : {selected}")
    for symbol, ref in selected.items():
        measure(ref, symbol, catalogue)
    print(
        "\nRappel : aucune de ces mesures n'est une promotion. Le plafond reste SIGNAL ou DEMO "
        "selon la derogation du manifeste, et les 9 portes n'ont pas ete franchies."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
