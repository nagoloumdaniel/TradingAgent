"""Ce que chaque filtre de la spec BTCUSD retire, et ce qu'il rapporte — mesuré, pas supposé.

**Pourquoi ce script existe.** La spec de l'opérateur demande six filtres ; la règle en
implémente trois. Ajouter les autres sans mesurer serait exactement l'erreur que ce dépôt
refuse : un filtre qui « a l'air juste » et qui retire des trades gagnants coûte plus cher que
le bruit qu'il prétend écarter. Ici, chaque filtre est éteint par défaut, et ce script mesure
ce qu'il change **quand on le rallume** — trades, réussite, net, PF, et combien de signaux il
a effectivement refusés.

**Le protocole est gelé** : BTCUSD, le **jeu complet** de `docs/research/datasets-volume`
(59 999 bougies M15), `ema_fast=20, ema_slow=50, vwap_period=20, atr_period=14,
stop_atr_multiplier=1.5, first_target_rr=1.5, final_target_rr=3.0, pullback_atr=0.4,
entry_zone_atr=0.1, min_slope_atr=0.005, slope_window=10`, sortie partielle 50/50 et passage à
break-even après le premier objectif. La référence (aucun filtre) doit rendre **883 trades,
40,2 % de réussite, -392,74 EUR, PF 0,9314**.

**Pourquoi le jeu complet et pas une fenêtre.** Les 20 000 dernières bougies donnent, sur la
même configuration, **268 trades et PF 0,900** — et une autre fenêtre de 20 000 donne 301 trades
et PF 0,990. Trois chiffres pour une seule règle : le découpage décide du résultat, et un filtre
choisi sur la fenêtre qui flatte serait choisi sur du bruit. Le jeu entier est la seule mesure
qui ne dépende pas d'un découpage ; `--bars 20000` reste disponible pour le vérifier.

**Pourquoi un balayage en plus des backtests.** Un backtest complet prend ~3 minutes ; savoir
*combien de signaux* un filtre refuse ne demande pas de simuler les remplissages. La passe de
comptage traverse donc la série une fois, en appelant la même fonction d'évaluation que le
harnais (`strategies.evaluation.evaluate`) **sur la même fenêtre glissante de 400 barres**, et
caractérise chaque filtre sur la population réelle des signaux : volume relatif, séance,
tendance. C'est ce qui permet de dire non seulement « ce filtre coûte 40 trades », mais « il
refuse 58 % des signaux ».

    uv run python scripts/backtest/tune_filter_vwap.py                 # tout, ~25 min
    uv run python scripts/backtest/tune_filter_vwap.py --bars 8000     # vérification rapide
    uv run python scripts/backtest/tune_filter_vwap.py --only "séance : overlap seul"
    uv run python scripts/backtest/tune_filter_vwap.py --skip-count    # backtests seuls
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from tradingagent.backtest.costs import CostModel
from tradingagent.backtest.datasets import CandleDataset, DatasetStore
from tradingagent.backtest.harness import BacktestConfig, decision_prefix, run_backtest
from tradingagent.core.market import Candle
from tradingagent.core.mode import TradingMode
from tradingagent.indicators.momentum import rsi
from tradingagent.indicators.regime import trend_of
from tradingagent.indicators.session import Session, session_at
from tradingagent.strategies.evaluation import OutcomeKind, evaluate
from tradingagent.strategies.library.vwap_pullback import VwapPullback, VwapPullbackParameters
from tradingagent.strategies.manifest import StrategyManifest

ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "docs" / "research" / "datasets-volume"
SYMBOL = "BTCUSD"

#: La référence à retrouver sur le jeu complet, telle que le lead l'a mesurée. Elle est écrite
#: ici pour être **comparée**, jamais visée : un chiffre qu'on cherche à atteindre finit par
#: être atteint par un réglage, et c'est exactement ce que cet axe doit éviter.
REFERENCE: dict[str, float] = {
    "trades": 883,
    "win_rate": 0.402,
    "net_eur": -392.74,
    "profit_factor": 0.9314,
}

#: Le protocole gelé, repris tel quel du rapport du 2026-10-09.
FROZEN: dict[str, Any] = {
    "ema_fast": 20,
    "ema_slow": 50,
    "vwap_period": 20,
    "atr_period": 14,
    "stop_atr_multiplier": 1.5,
    "first_target_rr": 1.5,
    "final_target_rr": 3.0,
    "pullback_atr": 0.4,
    "entry_zone_atr": 0.1,
    "min_slope_atr": 0.005,
    "slope_window": 10,
}

#: Les variantes mesurées. `None` = filtre éteint, c'est-à-dire la règle livrée.
VARIANTS: dict[str, dict[str, Any]] = {
    "référence (aucun filtre)": {},
    "séance : overlap + Londres + New York": {
        "allowed_sessions": (Session.OVERLAP, Session.LONDON, Session.NEW_YORK),
    },
    "séance : overlap seul": {"allowed_sessions": (Session.OVERLAP,)},
    "volume >= 1,0x la moyenne 20": {"volume_ratio_min": 1.0},
    "volume >= 1,2x la moyenne 20": {"volume_ratio_min": 1.2},
    "tendance : seuil du régime (0,05)": {"trend_filter": True},
    "tendance : seuil 0,001": {"trend_filter": True, "trend_slope_atr": 0.001},
    "séance UT + volume >= 1,0x": {
        "allowed_sessions": (Session.OVERLAP, Session.LONDON, Session.NEW_YORK),
        "volume_ratio_min": 1.0,
    },
}

RISK_EUR = Decimal("10")
COMMISSION_EUR = Decimal("0.5")

#: Coûts du dépôt : 0,5 point de base de spread, 0,2 de slippage, 0,50 € par opération.
SPREAD_BP = 5e-5
SLIPPAGE_BP = 2e-5

#: Fenêtre de la moyenne de volume relative, celle du défaut du modèle.
VOLUME_LOOKBACK = 20


def parameters(overrides: Mapping[str, Any]) -> VwapPullbackParameters:
    return VwapPullbackParameters.model_validate({**FROZEN, **overrides})


def manifest_for(dataset: CandleDataset) -> StrategyManifest:
    """Le manifeste de production, avec les paramètres gelés du protocole."""
    return StrategyManifest(
        strategy_id="vwap_pullback",
        version="1.0.0",
        max_mode=TradingMode.SIGNAL,
        allowed_symbols=(SYMBOL,),
        timeframes=(dataset.timeframe,),
        history_bars=400,
        expiry_bars=2,
        parameters=dict(FROZEN),
    )


def config_for(dataset: CandleDataset) -> BacktestConfig:
    """Le modèle de coûts du dépôt, et la géométrie de sortie demandée par la tâche."""
    price = dataset.candles[0].close
    return BacktestConfig(
        symbol=SYMBOL,
        costs=CostModel(
            spread=round(price * SPREAD_BP, 6),
            slippage_fixed=round(price * SLIPPAGE_BP, 6),
            commission_per_trade=COMMISSION_EUR,
        ),
        mode=TradingMode.SIGNAL,
        max_concurrent_positions=1,
        risk_eur=RISK_EUR,
        partial_exit_fractions=(0.5, 0.5),
        move_stop_to_breakeven_after_first_target=True,
    )


def measure(
    dataset: CandleDataset, overrides: Mapping[str, Any]
) -> tuple[dict[str, float], tuple[Any, ...]]:
    """Un backtest complet d'une variante, et ses chiffres."""
    frozen = parameters(overrides)
    result = run_backtest(
        VwapPullback(frozen),
        manifest_for(dataset),
        {dataset.timeframe: dataset.candles},
        config_for(dataset),
    )
    performance = result.performance
    trades = result.trades
    wins = sum(1 for trade in trades if trade.pnl_eur > 0)
    net = sum((trade.pnl_eur for trade in trades), Decimal(0))
    hours = sorted(trade.opened_at.hour + trade.opened_at.minute / 60 for trade in trades)
    return (
        {
            "trades": float(len(trades)),
            "wins": float(wins),
            "win_rate": (wins / len(trades)) if trades else 0.0,
            "net_eur": float(net),
            "profit_factor": float(performance.profit_factor or 0.0),
            "expectancy": float(performance.expectancy or 0.0),
            "max_drawdown": float(performance.max_drawdown),
            "signals": float(result.signals),
            "entries": float(result.entries),
            "skipped_no_room": float(result.skipped_no_room),
            "first_hour": hours[0] if hours else 0.0,
            "last_hour": hours[-1] if hours else 0.0,
        },
        trades,
    )


def count_signals(
    dataset: CandleDataset,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Une passe de comptage : quels signaux existent, et ce que chaque filtre en refuserait.

    Aucun remplissage n'est simulé : on appelle la même évaluation que le harnais, barre par
    barre, et on lit les mesures publiées par la règle. C'est la seule partie du protocole qui
    soit assez bon marché pour être balayée sur plusieurs seuils.
    """
    frozen = parameters({})
    rule = VwapPullback(frozen)
    manifest = manifest_for(dataset)
    series = dataset.candles
    total = 0
    sessions: dict[str, int] = {}
    ratios: list[float] = []
    trends: dict[str, int] = {}
    rsis: list[float] = []
    by_hour: dict[int, int] = {}

    for index, bar in enumerate(series):
        if index < manifest.history_bars - 1:
            continue
        candidate = _signal_at(rule, manifest, series, bar)
        if candidate is None:
            continue
        total += 1
        moment = bar.open_time
        sessions[str(session_at(moment))] = sessions.get(str(session_at(moment)), 0) + 1
        by_hour[moment.hour] = by_hour.get(moment.hour, 0) + 1
        ratios.append(float(candidate.indicators.get("volume_ratio", 0.0)))
        rsis.append(_rsi_of(series, index, bar.close))
        _tally_trend(trends, series, index, manifest.history_bars)

    above = {
        str(threshold): sum(1 for ratio in ratios if ratio >= threshold)
        for threshold in (0.8, 1.0, 1.2, 1.5)
    }
    # Le recensement ne se contente pas de décrire : il **compte les refus**, filtre par filtre,
    # sur la même fenêtre que le harnais. C'est ce qui permet de dire si un filtre retire peu ou
    # beaucoup avant même de payer un backtest.
    refused = {
        "séance hors fenêtres du bureau": sessions.get("off", 0),
        "tendance non nommée": trends.get("neutral", 0),
        "volume < 1,0x": total - above["1.0"],
        "volume < 1,2x": total - above["1.2"],
    }
    return (
        {
            "signals": total,
            "sessions": sessions,
            "by_hour": dict(sorted(by_hour.items())),
            "volume_ratio_median": _median(ratios),
            "volume_above": above,
            "trend": trends,
            "refused_by": refused,
            "rsi_median": _median(rsis),
        },
        {},
    )


def _signal_at(
    rule: VwapPullback,
    manifest: StrategyManifest,
    series: Sequence[Candle],
    bar: Candle,
) -> Any:
    """Le signal de la règle **sans filtre** à la clôture de `bar`, ou `None`.

    Le filtre de tendance est désactivé ici : la passe de comptage veut la population de
    signaux bruts, c'est-à-dire ce que la détection produit avant tout refus.
    """
    window = decision_prefix(series, bar.close_time)
    windows = {manifest.primary_timeframe: list(window)}
    outcome = evaluate(rule, manifest, SYMBOL, windows, bar.close_time)
    if outcome.kind is not OutcomeKind.SIGNAL:
        return None
    return outcome.candidate


def _rsi_of(series: Sequence[Candle], index: int, _close: float) -> float:
    """Le RSI(14) à cette barre, pour documenter ce que le filtre de momentum ne regarde pas."""
    window = [candle.close for candle in series[max(0, index - 60) : index + 1]]
    value = rsi(window, 14)[-1]
    return 50.0 if value is None else value


def _tally_trend(trends: dict[str, int], series: Sequence[Candle], index: int, bars: int) -> None:
    """La tendance du module de régime, lue **sur la même fenêtre que le harnais**.

    Le harnais ne donne à la règle que `history_bars` bougies closes ; compter la tendance sur
    tout l'historique disponible répondrait donc à une autre question, et le recensement
    n'expliquerait plus les trades mesurés. Le décalage a été constaté : 72 signaux annoncés par
    une fenêtre glissante de 200 barres, 37 trades par le harnais.
    """
    first = max(0, index - bars + 1)
    window = series[first : index + 1]
    highs = [candle.high for candle in window]
    lows = [candle.low for candle in window]
    closes = [candle.close for candle in window]
    label = str(trend_of(highs, lows, closes, fast=20, slow=50, atr_period=14))
    trends[label] = trends.get(label, 0) + 1


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def truncate(dataset: CandleDataset, bars: int) -> CandleDataset:
    """Les `bars` dernières bougies, identifiées comme telles.

    Le `dataset_id` est marqué : un jeu tronqué ne doit jamais être confondu avec le jeu gelé
    dont il vient, et la référence 301 trades/PF 0,990 ne vaut que pour 20 000 bougies.
    """
    if bars <= 0 or bars >= len(dataset.candles):
        return dataset
    return replace(
        dataset,
        dataset_id=f"{dataset.dataset_id}-tronque-{bars}",
        candles=dataset.candles[-bars:],
    )


def _use_utf8_when_redirected() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and not stream.isatty():
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _use_utf8_when_redirected()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", type=Path, default=DATASET_DIR)
    parser.add_argument(
        "--bars",
        type=int,
        default=0,
        help="ne garder que les N dernières bougies ; 0 = le jeu complet (défaut)",
    )
    parser.add_argument("--skip-count", action="store_true")
    parser.add_argument("--only", default=None, help="ne mesurer qu'une variante, par son nom")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    datasets = DatasetStore(args.datasets).load_all()
    if SYMBOL not in datasets:
        raise SystemExit(f"{SYMBOL} absent de {args.datasets}")
    # Le défaut est le **jeu complet**, pas une fenêtre : une fenêtre de 20 000 bougies a montré
    # PF 0,990 là où le jeu entier donne 0,9314. Comparer des filtres sur une fenêtre qui flatte
    # la référence ferait choisir un filtre sur du bruit de découpage.
    dataset = truncate(datasets[SYMBOL], args.bars)
    first, last = dataset.candles[0].open_time, dataset.candles[-1].open_time
    print(f"== {SYMBOL} {dataset.timeframe.value} — {len(dataset.candles)} bougies ==")
    print(f"   de {first.isoformat()} à {last.isoformat()} UTC")
    print(f"   jeu : {dataset.dataset_id}")
    print(f"   paramètres : {FROZEN}")
    print()

    payload: dict[str, Any] = {
        "symbol": SYMBOL,
        "bars": len(dataset.candles),
        "window_start": first.isoformat(),
        "window_end": last.isoformat(),
        "parameters": FROZEN,
        "variants": {},
    }

    if not args.skip_count:
        print("-- passe de comptage (aucun remplissage simulé) --")
        counts, _ = count_signals(dataset)
        payload["signal_census"] = counts
        print(f"   signaux bruts : {counts['signals']}")
        for name, value in counts["volume_above"].items():
            share = value / counts["signals"] if counts["signals"] else 0.0
            print(f"   volume >= {name}x : {value} ({share:.1%})")
        for name, value in counts["trend"].items():
            print(f"   tendance {name} : {value}")
        for name, value in counts["sessions"].items():
            print(f"   séance {name} : {value}")
        for name, value in counts["refused_by"].items():
            share = value / counts["signals"] if counts["signals"] else 0.0
            print(f"   refusés par « {name} » : {value} ({share:.1%})")
        print(f"   volume relatif médian : {counts['volume_ratio_median']:.3f}")
        print()

    header = (
        f"  {'variante':38} {'signaux':>7} {'trades':>6} {'réussite':>8} {'net EUR':>9} {'PF':>6}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for label, overrides in VARIANTS.items():
        if args.only is not None and label != args.only:
            continue
        stats, _ = measure(dataset, overrides)
        payload["variants"][label] = {**stats, "overrides": _plain(overrides)}
        print(
            f"  {label:38} {stats['signals']:>7.0f} {stats['trades']:>6.0f} "
            f"{stats['win_rate']:>7.1%} {stats['net_eur']:>9.2f} {stats['profit_factor']:>6.3f}"
        )
        if label == "référence (aucun filtre)":
            payload["reference_check"] = _reference_check(stats)
            print(f"   -> {payload['reference_check']}")

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"\nécrit : {args.output}")
    return 0


def _reference_check(stats: Mapping[str, float]) -> str:
    """Confronte la mesure sans filtre à celle du lead, et le dit en clair.

    Onze trades d'écart sur 883 (1,2 %) sont attendus : la mesure du lead vient d'un autre
    script. Ce qui compte est qu'on parle de **la même** référence, pas qu'on la recopie — un
    chiffre visé au trade près serait un chiffre ajusté.
    """
    gaps = []
    for key, expected in REFERENCE.items():
        actual = stats[key]
        tolerance = 0.02 * abs(expected) if key != "win_rate" else 0.01
        if abs(actual - expected) > tolerance:
            gaps.append(f"{key} : mesuré {actual:.4f}, annoncé {expected:.4f}")
    if gaps:
        return "DÉSALIGNÉ — " + " ; ".join(gaps)
    return "aligné sur la référence du lead (883 trades, 40,2 %, -392,74 EUR, PF 0,9314)"


def _plain(overrides: Mapping[str, Any]) -> dict[str, Any]:
    """JSON-safe : `Session` est un `StrEnum`, donc sérialisable tel quel ; les tuples non."""
    return {
        key: (list(value) if isinstance(value, tuple) else value)
        for key, value in overrides.items()
    }


if __name__ == "__main__":
    sys.exit(main())
