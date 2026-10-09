"""Ce que coûte `_entry_features` dans un vrai backtest — sans profileur, donc sans mentir.

**Pourquoi ce script existe.** `cProfile` gonfle énormément le coût des fonctions minuscules
appelées des millions de fois : il ajoute un événement par appel, et une fonction de trois
lignes peut y paraître dix fois plus chère qu'elle ne l'est. La proposition écrite de l'axe C
(voir `docs/research/vwap-tuning/axe-C-performance.md`) porte sur `_entry_features`, il fallait
donc un chiffre **de temps mural** : celui-ci.

`harness.py` n'est pas modifié. La fonction est enveloppée à l'exécution, dans ce processus
seulement, et rendue intacte : le backtest produit exactement le même résultat qu'en temps
normal, seule la mesure change.

    uv run python scripts/backtest/profile_axe_c_entry_features.py
    uv run python scripts/backtest/profile_axe_c_entry_features.py --bars 20000 --detail
"""

import argparse
import sys
import time
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from tradingagent.backtest.costs import CostModel
from tradingagent.backtest.datasets import CandleDataset, DatasetStore
from tradingagent.backtest.harness import BacktestConfig, run_backtest
from tradingagent.core.mode import TradingMode
from tradingagent.strategies.library.vwap_pullback import VwapPullback, VwapPullbackParameters
from tradingagent.strategies.manifest import StrategyManifest

ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "docs" / "research" / "datasets-volume"
REF = "vwap_pullback@1.0.0"
MANIFEST_PATH = ROOT / "config" / "strategies" / f"{REF}.yaml"

#: Les briques appelées par `features.entry_features`. Leur temps est **inclusif** : `trend_of`
#: appelle `slope_in_atr`, qui appelle `atr`, donc les lignes ne s'additionnent pas.
FEATURE_PARTS = (
    "atr",
    "atr_ratio",
    "trend_of",
    "slope_in_atr",
    "market_structure",
    "structure_of",
    "session_at",
)


def _use_utf8_when_redirected() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and not stream.isatty():
            reconfigure(encoding="utf-8", errors="replace")


def load_manifest() -> tuple[StrategyManifest, dict[str, float]]:
    import yaml

    with MANIFEST_PATH.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    return StrategyManifest.model_validate(document), dict(document["parameters"])


def config_for(symbol: str, dataset: CandleDataset) -> BacktestConfig:
    """Le modèle de coûts du dépôt, identique à `current_stats.py` : la mesure est comparable."""
    price = dataset.candles[0].close
    return BacktestConfig(
        symbol=symbol,
        costs=CostModel(
            spread=round(price * 0.00005, 6),
            slippage_fixed=round(price * 0.00002, 6),
            commission_per_trade=Decimal("0.5"),
        ),
        mode=TradingMode.SIGNAL,
        max_concurrent_positions=1,
    )


def main(argv: Sequence[str] | None = None) -> int:
    _use_utf8_when_redirected()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", type=Path, default=DATASET_DIR)
    parser.add_argument("--market", default="BTCUSD")
    parser.add_argument("--bars", type=int, default=0, help="ne garder que les N dernières bougies")
    parser.add_argument("--detail", action="store_true", help="détailler les briques appelées")
    args = parser.parse_args(argv)

    import tradingagent.backtest.harness as harness
    import tradingagent.indicators.features as features

    totals: dict[str, float] = {}
    counts: dict[str, int] = {}

    def wrap(module: Any, name: str) -> None:
        original = getattr(module, name)

        def timed(*positional: Any, **keywords: Any) -> Any:
            started = time.perf_counter()
            try:
                return original(*positional, **keywords)
            finally:
                totals[name] = totals.get(name, 0.0) + (time.perf_counter() - started)
                counts[name] = counts.get(name, 0) + 1

        setattr(module, name, timed)

    if args.detail:
        for part in FEATURE_PARTS:
            wrap(features, part)

    original_entry_features = harness._entry_features
    calls: list[tuple[int, float]] = []

    def timed_entry_features(primary: Any, index: int, config: Any) -> Any:
        started = time.perf_counter()
        result = original_entry_features(primary, index, config)
        calls.append((index, time.perf_counter() - started))
        return result

    datasets = DatasetStore(args.datasets).load_all()
    if args.market not in datasets:
        raise SystemExit(f"{args.market}: aucun jeu gelé dans {args.datasets}")
    dataset = datasets[args.market]
    candles = dataset.candles[-args.bars :] if args.bars > 0 else dataset.candles
    manifest, parameters = load_manifest()
    strategy = VwapPullback(VwapPullbackParameters(**parameters))
    config = config_for(args.market, dataset)

    harness._entry_features = timed_entry_features
    started = time.perf_counter()
    result = run_backtest(strategy, manifest, {manifest.primary_timeframe: candles}, config)
    elapsed = time.perf_counter() - started
    harness._entry_features = original_entry_features

    total = sum(duration for _, duration in calls)
    print(f"bougies                 : {len(candles)}")
    print(f"opérations              : {result.performance.trades}")
    print(f"backtest complet        : {elapsed:8.1f} s")
    print(f"_entry_features appels  : {len(calls)}")
    print(f"_entry_features total   : {total:8.1f} s   ({total / elapsed * 100:.1f} % du run)")
    if calls:
        print(f"coût moyen par appel    : {total / len(calls) * 1000:.1f} ms")
        lengths = sorted(index for index, _ in calls)
        print(
            f"index des remplissages  : min {lengths[0]}, médian {lengths[len(lengths) // 2]}, "
            f"max {lengths[-1]}"
        )
        early = [duration for index, duration in calls if index < 5_000]
        late = [duration for index, duration in calls if index >= 15_000]
        if early:
            print(f"  avant 5 000 barres    : {sum(early) / len(early) * 1000:8.1f} ms par appel")
        if late:
            print(f"  après 15 000 barres   : {sum(late) / len(late) * 1000:8.1f} ms par appel")
    if args.detail:
        print(
            "-- briques appelées par features.entry_features (temps inclusif, appels imbriqués) --"
        )
        for name, value in sorted(totals.items(), key=lambda item: -item[1]):
            print(f"  {name:<18} {value:8.2f} s   {counts[name]:6d} appels")
    return 0


if __name__ == "__main__":
    sys.exit(main())
