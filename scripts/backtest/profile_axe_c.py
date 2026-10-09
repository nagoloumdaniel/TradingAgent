"""Le profileur de l'axe C : où partent les ~80 secondes d'un backtest de 20 000 bougies.

Ce script existe pour répondre à une seule question, et il y répond de deux façons :

* `--profile` lance `cProfile` sur `run_backtest` et imprime les fonctions triées par temps
  **cumulé** (la vue qui montre la chaîne d'appel) puis par temps **propre** (la vue qui
  montre qui brûle réellement le CPU) ;
* sans `--profile`, il imprime le temps mural et un **digest** canonique du résultat : nombre
  d'opérations, facteur de profit, prix de sortie, PnL, excursions. Deux exécutions qui
  donnent le même digest ont produit exactement les mêmes opérations — c'est la mesure qui
  autorise une optimisation, et celle qui l'interdit si le digest bouge.

Le jeu de données est celui que la campagne utilise (`--datasets`), la stratégie et ses
paramètres sont lus dans son manifeste de production : rien n'est recopié ici, sinon la mesure
mesurerait autre chose que ce que l'agent exécute.

    uv run python scripts/backtest/profile_axe_c.py --profile
    uv run python scripts/backtest/profile_axe_c.py --bars 4000
    uv run python scripts/backtest/profile_axe_c.py --digest --repeat 3
"""

import argparse
import cProfile
import hashlib
import json
import pstats
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from tradingagent.analytics.model import Performance, Trade
from tradingagent.backtest.costs import CostModel
from tradingagent.backtest.datasets import CandleDataset, DatasetStore
from tradingagent.backtest.harness import BacktestConfig, BacktestResult, run_backtest
from tradingagent.core.mode import TradingMode
from tradingagent.strategies.library.vwap_pullback import VwapPullback, VwapPullbackParameters
from tradingagent.strategies.manifest import StrategyManifest

ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "docs" / "research" / "datasets-volume"
REF = "vwap_pullback@1.0.0"
MANIFEST_PATH = ROOT / "config" / "strategies" / f"{REF}.yaml"

#: Les modules qui définissent le chemin mesuré. Leur empreinte est imprimée avec la mesure :
#: deux exécutions ne se comparent que si ces empreintes sont identiques, et d'autres agents
#: écrivent dans ce dépôt pendant la mesure. Elles sont lues sur les modules **réellement
#: importés**, pas sur un chemin recopié : un A/B qui fait pointer `PYTHONPATH` ailleurs (un
#: worktree, par exemple) s'imprime alors lui-même comme tel, au lieu de mentir.
MEASURED_MODULES = (
    "tradingagent.indicators._checks",
    "tradingagent.indicators.volatility",
    "tradingagent.indicators.vwap",
    "tradingagent.indicators.moving_average",
    "tradingagent.indicators.features",
    "tradingagent.backtest.harness",
    "tradingagent.strategies.evaluation",
    "tradingagent.strategies.library.vwap_pullback",
)


def source_hashes() -> dict[str, str]:
    """L'empreinte SHA-256 du fichier source de chaque module mesuré, préfixe de 12 caractères."""
    import importlib
    import inspect

    fingerprints: dict[str, str] = {}
    for name in MEASURED_MODULES:
        module = importlib.import_module(name)
        path = inspect.getsourcefile(module)
        if path is None:
            fingerprints[name.rsplit(".", 1)[-1]] = "inconnu"
            continue
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
        fingerprints[Path(path).name] = digest
    return fingerprints


def load_manifest() -> tuple[StrategyManifest, dict[str, float]]:
    """Le manifeste de production, tel qu'écrit : le nommer ne suffit pas, il faut le lire."""
    import yaml

    with MANIFEST_PATH.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    return StrategyManifest.model_validate(document), dict(document["parameters"])


def config_for(symbol: str, dataset: CandleDataset) -> BacktestConfig:
    """Le modèle de coûts du dépôt, identique à `current_stats.py` (spread 0,5 bp, 0,2 bp)."""
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


def trade_line(trade: Trade) -> str:
    """Une opération entière sur une ligne, en `repr` : le bit près, pas l'arrondi d'affichage."""
    features = ",".join(f"{key}={value!r}" for key, value in sorted(trade.features.items()))
    return "|".join(
        (
            trade.opened_at.isoformat(),
            trade.closed_at.isoformat(),
            str(trade.direction),
            str(trade.pnl_eur),
            str(trade.risk_eur),
            repr(trade.slippage),
            repr(trade.spread),
            repr(trade.mae_r),
            repr(trade.mfe_r),
            features,
        )
    )


def performance_line(performance: Performance) -> str:
    return json.dumps(
        {
            key: repr(value)
            for key, value in performance.__dict__.items()
            if not key.startswith("_")
        },
        sort_keys=True,
    )


def digest_of(result: BacktestResult) -> str:
    """L'empreinte de tout ce qui est observable : chaque opération, puis chaque statistique."""
    payload = "\n".join(
        [
            f"trades={result.performance.trades}",
            f"entries={result.entries}",
            f"signals={result.signals}",
            f"decisions={result.decisions}",
            performance_line(result.performance),
            *(trade_line(trade) for trade in result.trades),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def run_once(
    symbol: str, dataset: CandleDataset, manifest: StrategyManifest, parameters: Mapping[str, Any]
) -> tuple[BacktestResult, float]:
    strategy = VwapPullback(VwapPullbackParameters(**parameters))
    config = config_for(symbol, dataset)
    started = time.perf_counter()
    result = run_backtest(strategy, manifest, {manifest.primary_timeframe: dataset.candles}, config)
    return result, time.perf_counter() - started


def profile_once(
    symbol: str, dataset: CandleDataset, manifest: StrategyManifest, parameters: Mapping[str, Any]
) -> tuple[BacktestResult, float, cProfile.Profile]:
    strategy = VwapPullback(VwapPullbackParameters(**parameters))
    config = config_for(symbol, dataset)
    profiler = cProfile.Profile()
    started = time.perf_counter()
    profiler.enable()
    result = run_backtest(strategy, manifest, {manifest.primary_timeframe: dataset.candles}, config)
    profiler.disable()
    return result, time.perf_counter() - started, profiler


def print_stats(profiler: cProfile.Profile, top: int, sort: str) -> None:
    print(f"\n== top {top} par {sort} " + "=" * (58 - len(sort)))
    stats = pstats.Stats(profiler)
    stats.sort_stats(sort).print_stats(top)


def benchmark_indicators(dataset: CandleDataset, iterations: int) -> None:
    """Le coût intrinsèque de la fenêtre que la stratégie recalcule à chaque barre.

    Un backtest entier dépend de la charge de la machine (d'autres agents mesurent en même
    temps), donc son temps mural ne prouve rien à lui seul. Ce banc-ci isole l'unité de travail
    — 400 bougies passées à `vwap` puis à `atr` — et rend un coût par appel qu'un avant/après
    peut comparer même sous charge.
    """
    from tradingagent.indicators.volatility import atr
    from tradingagent.indicators.vwap import vwap

    window = list(dataset.candles[:400])
    moments = [candle.open_time for candle in window]
    highs = [candle.high for candle in window]
    lows = [candle.low for candle in window]
    closes = [candle.close for candle in window]
    volumes = [float(candle.volume) for candle in window if candle.volume is not None]

    def measure(label: str, rounds: int, work: Callable[[], object]) -> None:
        work()  # chauffe : la première exécution paie l'import et le cache d'octets
        started = time.perf_counter()
        for _ in range(rounds):
            work()
        elapsed = (time.perf_counter() - started) / rounds
        print(f"banc {label:<16}: {elapsed * 1e6:8.1f} µs par appel")

    rounds = max(iterations, 1)
    measure("vwap(400)", rounds, lambda: vwap(moments, highs, lows, closes, volumes))
    measure("atr(400)", rounds, lambda: atr(highs, lows, closes, 14))
    measure(
        "vwap+atr",
        rounds,
        lambda: (vwap(moments, highs, lows, closes, volumes), atr(highs, lows, closes, 14)),
    )


def _use_utf8_when_redirected() -> None:
    """Un tube Windows redirigé retombe sur une page de code héritée, qui ne sait pas écrire
    une flèche. Demander l'UTF-8 plutôt que de planter au milieu d'un rapport."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and not stream.isatty():
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _use_utf8_when_redirected()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", type=Path, default=DATASET_DIR)
    parser.add_argument("--market", default="BTCUSD")
    parser.add_argument("--bars", type=int, default=0, help="ne garder que les N dernières bougies")
    parser.add_argument("--profile", action="store_true", help="profiler avec cProfile")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--repeat", type=int, default=1, help="répéter la mesure (temps mural)")
    parser.add_argument("--digest", action="store_true", help="imprimer le digest du résultat")
    parser.add_argument(
        "--bench",
        type=int,
        default=0,
        metavar="N",
        help="banc d'essai : N appels de vwap(400) et atr(400), sans backtest",
    )
    args = parser.parse_args(argv)

    datasets = DatasetStore(args.datasets).load_all()
    if args.market not in datasets:
        raise SystemExit(f"{args.market}: aucun jeu gelé dans {args.datasets}")
    dataset = datasets[args.market]
    if args.bars > 0:
        dataset = replace(dataset, candles=dataset.candles[-args.bars :])
    manifest, parameters = load_manifest()

    print(f"marché          : {args.market}")
    print(f"bougies         : {len(dataset.candles)}")
    print(f"empreinte jeu   : {dataset.fingerprint[:12]}")
    print(f"history_bars    : {manifest.history_bars}")
    print(f"stratégie       : {REF} {dict(parameters)}")
    print(f"fenêtre         : {dataset.candles[0].open_time} → {dataset.candles[-1].open_time}")
    print(
        "sources         : "
        + " ".join(f"{name}={value}" for name, value in source_hashes().items())
    )

    if args.bench > 0:
        benchmark_indicators(dataset, args.bench)
        return 0

    timings: list[float] = []
    result: BacktestResult | None = None
    profiler: cProfile.Profile | None = None
    for attempt in range(max(args.repeat, 1)):
        if args.profile and attempt == 0:
            result, elapsed, profiler = profile_once(args.market, dataset, manifest, parameters)
        else:
            result, elapsed = run_once(args.market, dataset, manifest, parameters)
        timings.append(elapsed)
        print(f"exécution {attempt + 1}   : {elapsed:.3f} s")

    if result is None:  # pragma: no cover - garde de script
        raise SystemExit("le backtest de reference n'a rien produit")
    print(
        f"décisions       : {result.decisions}\n"
        f"signaux         : {result.signals}\n"
        f"opérations      : {result.performance.trades}\n"
        f"facteur profit  : {result.performance.profit_factor}\n"
        f"profit net      : {result.performance.net_profit}\n"
        f"historique court: {result.insufficient_history}\n"
        f"signaux invalides: {result.invalid_signals}\n"
        f"erreurs stratégie: {len(result.strategy_errors)}\n"
        f"expirés/sans place: {result.expired_signals}/{result.skipped_no_room}\n"
        f"clôtures forcées: {result.forced_closures}"
    )
    if args.digest:
        print(f"digest          : {digest_of(result)}")
    if len(timings) > 1:
        print(f"meilleur temps  : {min(timings):.3f} s")
        print(f"médiane         : {sorted(timings)[len(timings) // 2]:.3f} s")
    if profiler is not None:
        print_stats(profiler, args.top, "cumulative")
        print_stats(profiler, args.top, "tottime")
    return 0


if __name__ == "__main__":
    sys.exit(main())
