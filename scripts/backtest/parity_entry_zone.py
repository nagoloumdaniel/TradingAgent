"""La porte `entry_zone` telle que la production la pose, mesurée sur le jeu complet BTCUSD.

**Pourquoi ce script existe.** `risk.checks.check_entry_zone` refuse un signal dont le prix
exécutable sort de la bande publiée par la stratégie. Le harnais de backtest ne l'a jamais
appliquée : il remplissait à un demi-spread et prenait tout ce qui touchait la bande. L'effet
réel de `entry_zone_atr` n'avait donc jamais été mesuré par aucune campagne. Ce script le
mesure, et chiffre les options de correction pour que l'opérateur tranche sur des nombres.

**Ce qu'il fait, dans l'ordre :**

1. rejoue la configuration gelée du protocole (59 999 bougies, coûts du dépôt) **porte
   éteinte** — c'est la référence ;
2. la rejoue **porte allumée**, puis apparie les deux populations de trades par `opened_at` :
   ce que la porte a refusé, ce que ces trades valaient, et de combien le prix payé dépassait
   la bande (en dollars et en ATR) ;
3. chiffre les options : **A** (bande élargie), **B** (bande décalée du spread, mesurée via la
   base `reference` du harnais), et la même mesure avec le **spread réellement observé** en
   production le 2026-10-09 (18,424 $), qui vaut 4,5 fois celui du modèle de coûts du dépôt.

    uv run python scripts/backtest/parity_entry_zone.py                     # tout, ~10 min
    uv run python scripts/backtest/parity_entry_zone.py --bars 8000 --only baseline,parity_paid

**Aucun ordre n'est envoyé, aucune base n'est touchée** : c'est un backtest hors ligne.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from tradingagent.analytics.model import Trade
from tradingagent.backtest.costs import CostModel
from tradingagent.backtest.datasets import CandleDataset, DatasetStore
from tradingagent.backtest.harness import (
    BacktestConfig,
    BacktestResult,
    decision_prefix,
    run_backtest,
)
from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.signal import SignalCandidate
from tradingagent.strategies.evaluation import OutcomeKind, evaluate
from tradingagent.strategies.manifest import StrategyManifest

ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "docs" / "research" / "datasets-volume"
#: La règle gelée à `bfa0e65`, copiée pour que les axes de réglage ne déplacent pas la mesure.
PINNED_STRATEGY = ROOT / "docs" / "research" / "vwap-tuning" / "vwap_pullback_pinned_bfa0e65.py"
SYMBOL = "BTCUSD"
OUTPUT = ROOT / "docs" / "research" / "execution-diagnostic" / "tools" / "parity-entry-zone.json"

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

#: Ce que la référence du lead doit rendre, porte éteinte, jeu complet. Comparée, jamais visée.
REFERENCE: dict[str, float] = {
    "trades": 883,
    "win_rate": 0.402,
    "net_eur": -392.74,
    "profit_factor": 0.9314,
}

RISK_EUR = Decimal("10")
COMMISSION_EUR = Decimal("0.5")
#: Les coûts du dépôt : 0,5 point de base de spread, 0,2 de slippage, 0,50 EUR par opération.
SPREAD_BP = 5e-5
SLIPPAGE_BP = 2e-5
#: Le spread réellement coté sur le compte de démonstration le 2026-10-09, en dollars. Il est
#: constant sur les onze décisions BTCUSD enregistrées (18,424 $ huit fois, jusqu'à 21,031 $),
#: donc traité ici comme un montant fixe et non comme une fraction du prix.
PRODUCTION_SPREAD_USD = 18.424

#: Les largeurs de bande de la courbe de survie (en multiples d'ATR), pour situer l'option A
#: sans rejouer un backtest par valeur : 0,1 est le réglage livré.
WIDTH_GRID: tuple[float, ...] = (0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4)


@dataclass(frozen=True)
class Scenario:
    """Une mesure : la même stratégie, un seul réglage qui change."""

    name: str
    label: str
    parity: bool = False
    basis: str = "paid"
    overrides: Mapping[str, Any] = field(default_factory=dict)
    production_spread: bool = False


SCENARIOS: tuple[Scenario, ...] = (
    # L'ordre compte : sous charge, la mesure la plus décisive doit être écrite en premier, et
    # le JSON est réécrit après chaque scénario. Le spread observé d'abord, parce que c'est le
    # monde réel ; les coûts du dépôt ensuite, parce que c'est la référence du protocole.
    Scenario(
        "baseline_prod_spread",
        "référence avec le spread observé (18,424 $)",
        production_spread=True,
    ),
    Scenario(
        "parity_paid_prod_spread",
        "porte allumée avec le spread observé",
        parity=True,
        production_spread=True,
    ),
    Scenario(
        "parity_production_prod_spread",
        "porte au prix réel (ask entier), spread observé",
        parity=True,
        basis="production",
        production_spread=True,
    ),
    Scenario("baseline", "référence, porte éteinte"),
    Scenario("parity_paid", "porte allumée (prix payé)", parity=True),
    Scenario(
        "parity_zone_0.2_prod_spread",
        "option A (0,2 ATR) avec le spread observé",
        parity=True,
        overrides={"entry_zone_atr": 0.2},
        production_spread=True,
    ),
    Scenario(
        "parity_zone_0.15_prod_spread",
        "option A calibrée (0,15 ATR), spread observé",
        parity=True,
        overrides={"entry_zone_atr": 0.15},
        production_spread=True,
    ),
    Scenario(
        "parity_zone_0.13_production",
        "option C : bande 0,13 ATR jugée au prix réel",
        parity=True,
        basis="production",
        overrides={"entry_zone_atr": 0.13},
        production_spread=True,
    ),
    Scenario(
        "parity_zone_0.3_prod_spread",
        "option A (0,3 ATR) avec le spread observé",
        parity=True,
        overrides={"entry_zone_atr": 0.3},
        production_spread=True,
    ),
    Scenario(
        "parity_reference_prod_spread",
        "option B avec le spread observé",
        parity=True,
        basis="reference",
        production_spread=True,
    ),
    Scenario(
        "parity_reference",
        "option B : bande décalée du spread",
        parity=True,
        basis="reference",
    ),
)


@dataclass
class Run:
    """Une mesure, gardée en mémoire pour l'appariement avec sa jumelle."""

    scenario: Scenario
    result: BacktestResult
    costs: CostModel
    bars: int

    @property
    def trades(self) -> tuple[Trade, ...]:
        return self.result.trades

    def aggregate(self) -> dict[str, Any]:
        performance = self.result.performance
        return {
            "label": self.scenario.label,
            "parity": self.scenario.parity,
            "basis": self.scenario.basis,
            "entry_zone_atr": float(self.scenario.overrides.get("entry_zone_atr", 0.1)),
            "spread_usd": self.costs.spread,
            "trades": len(self.trades),
            "wins": sum(1 for trade in self.trades if trade.pnl_eur > 0),
            "win_rate": _ratio(
                sum(1 for trade in self.trades if trade.pnl_eur > 0), len(self.trades)
            ),
            "net_eur": float(sum((trade.pnl_eur for trade in self.trades), Decimal(0))),
            "profit_factor": None
            if performance.profit_factor is None
            else float(performance.profit_factor),
            "expectancy": None if performance.expectancy is None else float(performance.expectancy),
            "max_drawdown": float(performance.max_drawdown),
            "signals": self.result.signals,
            "entries": self.result.entries,
            "skipped_no_room": self.result.skipped_no_room,
            "expired_signals": self.result.expired_signals,
            "refused_entry_zone": self.result.refused_entry_zone,
            "forced_closures": self.result.forced_closures,
        }


def _ratio(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def load_strategy_module() -> Any:
    """La règle gelée, importée par chemin : aucune modification d'un autre axe ne la déplace."""
    spec = importlib.util.spec_from_file_location("vwap_pullback_pinned", PINNED_STRATEGY)
    if spec is None or spec.loader is None:
        raise SystemExit(f"stratégie gelée illisible : {PINNED_STRATEGY}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest_for(dataset: CandleDataset) -> StrategyManifest:
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


def costs_for(dataset: CandleDataset, scenario: Scenario) -> CostModel:
    price = dataset.candles[0].close
    spread = PRODUCTION_SPREAD_USD if scenario.production_spread else round(price * SPREAD_BP, 6)
    return CostModel(
        spread=spread,
        slippage_fixed=round(price * SLIPPAGE_BP, 6),
        commission_per_trade=COMMISSION_EUR,
    )


def config_for(dataset: CandleDataset, scenario: Scenario) -> BacktestConfig:
    return BacktestConfig(
        symbol=SYMBOL,
        costs=costs_for(dataset, scenario),
        mode=TradingMode.SIGNAL,
        max_concurrent_positions=1,
        risk_eur=RISK_EUR,
        partial_exit_fractions=(0.5, 0.5),
        move_stop_to_breakeven_after_first_target=True,
        entry_zone_parity=scenario.parity,
        entry_zone_parity_basis=scenario.basis,  # type: ignore[arg-type]
    )


def measure(dataset: CandleDataset, scenario: Scenario, module: Any) -> Run:
    parameters = module.VwapPullbackParameters.model_validate({**FROZEN, **scenario.overrides})
    result = run_backtest(
        module.VwapPullback(parameters),
        manifest_for(dataset),
        {dataset.timeframe: dataset.candles},
        config_for(dataset, scenario),
    )
    return Run(
        scenario=scenario,
        result=result,
        costs=costs_for(dataset, scenario),
        bars=len(dataset.candles),
    )


# -- ce que la porte refuse, et ce que cela vaut --------------------------------------------


def verdicts(
    reference: Run,
    dataset: CandleDataset,
    module: Any,
    costs: CostModel,
) -> dict[str, Any]:
    """Le verdict de la porte pour **chaque** trade de la référence, sans rejouer la simulation.

    C'est la mesure qui répond à la question posée, et elle est exacte par construction : on
    reste dans le run de référence, donc aucune interaction de créneaux ne vient brouiller
    l'attribution. Pour chaque trade, la bande du signal est recalculée par `evaluate` sur la
    barre de décision, le prix payé par la formule du harnais (`_try_enter`), et la porte rend
    son verdict. Les trades refusés sont ceux que la production n'aurait jamais pris : leur
    `pnl_eur` est donc exactement ce que la porte coûte — ou rapporte.

    La reconstruction se vérifie elle-même : chaque trade déclaré refusé doit avoir un prix payé
    hors bande, et le nombre de refus doit retomber sur le compteur du harnais quand la porte
    agit. Les deux écarts sont comptés et publiés, jamais tus.
    """
    if costs.slippage_atr_fraction > 0:
        raise SystemExit("la reconstruction exige un slippage sans terme d'ATR")
    candles = dataset.candles
    index_of = {candle.close_time: index for index, candle in enumerate(candles)}
    manifest = manifest_for(dataset)
    rows: list[dict[str, Any]] = []
    unmatched = 0
    for trade in reference.trades:
        entry_index = index_of.get(trade.opened_at)
        if entry_index is None or entry_index == 0:
            unmatched += 1
            continue
        candidate = signal_at(module, manifest, candles, entry_index - 1)
        if candidate is None:
            unmatched += 1
            continue
        bar = candles[entry_index]
        if trade.direction is Direction.BUY:
            reference_price = min(bar.open, candidate.entry_high)
        else:
            reference_price = max(bar.open, candidate.entry_low)
        paid = costs.fill_price(reference_price, trade.direction, None)
        # Le harnais ne facture qu'un demi-spread (« la moitié de chaque côté », costs.py). La
        # production, elle, paie l'ask entier à l'achat et vend au bid : c'est la base
        # `production`, plus fidèle, qui dit ce que la porte refuserait pour de vrai.
        slippage = costs.slippage(None)
        if trade.direction is Direction.BUY:
            production_price = reference_price + costs.spread + slippage
        else:
            production_price = reference_price - slippage
        atr = float(candidate.indicators.get("atr") or 0.0) or None
        close = candles[entry_index - 1].close
        # La règle de production à l'instant du signal : l'ask (bid + spread) contre la bande.
        # Pour une vente le prix exécutable est le bid, donc la clôture elle-même.
        executable = close + costs.spread if trade.direction is Direction.BUY else close
        bound = candidate.entry_high if trade.direction is Direction.BUY else candidate.entry_low
        rows.append(
            {
                "opened_at": trade.opened_at.isoformat(),
                "direction": trade.direction.value,
                "close": close,
                "atr": atr,
                "band_usd": candidate.entry_high - candidate.entry_low,
                "reference": reference_price,
                "paid": paid,
                "pnl_eur": float(trade.pnl_eur),
                "paid_inside": candidate.entry_low <= paid <= candidate.entry_high,
                "production_inside": (
                    candidate.entry_low <= production_price <= candidate.entry_high
                ),
                "reference_inside": (
                    candidate.entry_low <= reference_price <= candidate.entry_high
                ),
                "paid_vs_close_usd": (
                    (paid - close) if trade.direction is Direction.BUY else (close - paid)
                ),
                "structural_rule_accepts": (
                    executable <= bound if trade.direction is Direction.BUY else executable >= bound
                ),
            }
        )

    def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        pnls = [Decimal(str(row["pnl_eur"])) for row in rows]
        wins = sum(1 for value in pnls if value > 0)
        gains = sum((value for value in pnls if value > 0), Decimal(0))
        losses = -sum((value for value in pnls if value < 0), Decimal(0))
        return {
            "count": len(rows),
            "wins": wins,
            "losses": len(rows) - wins,
            "win_rate": _ratio(wins, len(rows)),
            "net_eur": float(sum(pnls, Decimal(0))),
            "gross_gain_eur": float(gains),
            "gross_loss_eur": float(losses),
            "profit_factor": float(gains / losses) if losses else None,
            "average_eur": float(sum(pnls, Decimal(0)) / len(pnls)) if pnls else 0.0,
            "best_eur": float(max(pnls)) if pnls else 0.0,
            "worst_eur": float(min(pnls)) if pnls else 0.0,
            "by_direction": {
                side.value: sum(1 for row in rows if row["direction"] == side.value)
                for side in Direction
            },
        }

    refused = [row for row in rows if not row["paid_inside"]]
    taken = [row for row in rows if row["paid_inside"]]
    overshoots = [
        (
            (row["paid"] - row["close"])
            if row["direction"] == Direction.BUY.value
            else (row["close"] - row["paid"])
        )
        - float(row["band_usd"]) / 2
        for row in refused
    ]
    curve = {
        f"{width:.2f}": _summary(
            [
                row
                for row in rows
                if abs(row["paid_vs_close_usd"]) <= width * float(row["atr"] or 0.0)
            ]
        )
        for width in WIDTH_GRID
    }
    on_the_reference = [row for row in rows if not row["reference_inside"]]
    as_production_pays = [row for row in rows if not row["production_inside"]]
    structurally_refused = [row for row in rows if not row["structural_rule_accepts"]]
    return {
        "trades_examined": len(rows),
        "unmatched_signals": unmatched,
        "reconstruction_mismatches": sum(1 for value in overshoots if value <= 0),
        "refused": _summary(refused),
        "taken": _summary(taken),
        "refused_share_of_trades": _ratio(len(refused), len(rows)),
        "refused_overshoot_usd_median": _median(overshoots),
        "refused_overshoot_usd_max": max(overshoots, default=0.0),
        "band_over_spread_median": _median(
            [float(row["band_usd"]) / costs.spread for row in rows if costs.spread]
        ),
        "spread_usd": costs.spread,
        "reference_basis": {
            "refused": _summary(on_the_reference),
            "share": _ratio(len(on_the_reference), len(rows)),
        },
        "production_basis": {
            "refused": _summary(as_production_pays),
            "share": _ratio(len(as_production_pays), len(rows)),
        },
        "structural_rule": {
            "refused": _summary(structurally_refused),
            "share": _ratio(len(structurally_refused), len(rows)),
        },
        "width_curve": curve,
        "sample": refused[:8],
    }


def suppression(reference: Run, gated: Run) -> dict[str, Any]:
    """L'effet net de la porte quand elle agit, et l'interaction qu'elle provoque.

    Une entrée refusée libère un créneau : un signal que la référence avait écarté faute de
    place peut alors être pris. C'est pourquoi trois comptes différents sont publiés —
    `refused_by_counter` (les refus), `suppressed` (les trades de la référence absents du run
    avec porte) et `created` (les trades que la porte a fait naître) — au lieu d'un seul chiffre
    qui ferait croire à une soustraction propre.
    """
    reference_by_time = {trade.opened_at: trade for trade in reference.trades}
    if len(reference_by_time) != len(reference.trades):
        raise SystemExit("deux trades de la référence partagent un opened_at")
    gated_by_time = {trade.opened_at: trade for trade in gated.trades}
    suppressed = [
        trade for at, trade in sorted(reference_by_time.items()) if at not in gated_by_time
    ]
    created = [trade for at, trade in sorted(gated_by_time.items()) if at not in reference_by_time]
    return {
        "reference_trades": len(reference.trades),
        "gated_trades": len(gated.trades),
        "refused_by_counter": gated.result.refused_entry_zone,
        "suppressed": len(suppressed),
        "created": len(created),
        "identity_holds": len(reference.trades) - len(gated.trades)
        == len(suppressed) - len(created),
        "suppressed_net_eur": float(sum((trade.pnl_eur for trade in suppressed), Decimal(0))),
        "created_net_eur": float(sum((trade.pnl_eur for trade in created), Decimal(0))),
        "suppressed_win_rate": _ratio(
            sum(1 for trade in suppressed if trade.pnl_eur > 0), len(suppressed)
        ),
        "created_win_rate": _ratio(sum(1 for trade in created if trade.pnl_eur > 0), len(created)),
    }


def signal_at(
    module: Any,
    manifest: StrategyManifest,
    candles: Sequence[Candle],
    index: int,
) -> SignalCandidate | None:
    """Le signal tel que la production le voit à la clôture de la barre `index`."""
    evaluated_at = candles[index].close_time
    windows = {manifest.primary_timeframe: decision_prefix(candles, evaluated_at)}
    outcome = evaluate(
        module.VwapPullback(module.VwapPullbackParameters.model_validate(FROZEN)),
        manifest,
        SYMBOL,
        windows,
        evaluated_at,
    )
    return outcome.candidate if outcome.kind is OutcomeKind.SIGNAL else None


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


# -- exécution -----------------------------------------------------------------------------


def truncate(dataset: CandleDataset, bars: int) -> CandleDataset:
    """Les `bars` dernières bougies, marquées : un jeu tronqué n'est pas le jeu gelé."""
    if bars <= 0 or bars >= len(dataset.candles):
        return dataset
    return replace(
        dataset,
        dataset_id=f"{dataset.dataset_id}-tronque-{bars}",
        candles=dataset.candles[-bars:],
    )


def main(argv: Sequence[str] | None = None) -> int:
    _use_utf8_when_redirected()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", type=Path, default=DATASET_DIR)
    parser.add_argument("--bars", type=int, default=0, help="0 = le jeu complet (défaut)")
    parser.add_argument("--only", default=None, help="noms de scénarios, séparés par des virgules")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)

    datasets = DatasetStore(args.datasets).load_all()
    if SYMBOL not in datasets:
        raise SystemExit(f"{SYMBOL} absent de {args.datasets}")
    dataset = truncate(datasets[SYMBOL], args.bars)
    module = load_strategy_module()
    wanted = None if args.only is None else {name.strip() for name in args.only.split(",")}

    print(f"== {SYMBOL} {dataset.timeframe.value} — {len(dataset.candles)} bougies ==")
    print(f"   jeu : {dataset.dataset_id}")
    print(f"   règle gelée : {PINNED_STRATEGY.name}")
    print(f"   paramètres : {FROZEN}")
    print()

    runs: dict[str, Run] = {}
    payload: dict[str, Any] = {
        "symbol": SYMBOL,
        "bars": len(dataset.candles),
        "dataset_id": dataset.dataset_id,
        "parameters": FROZEN,
        "reference_expected": REFERENCE,
        "scenarios": {},
    }

    def checkpoint() -> None:
        """Écrit le JSON après chaque étape : sous charge, un run interrompu ne perd rien."""
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    header = (
        f"  {'scénario':42} {'trades':>6} {'réussite':>8} {'net EUR':>9} {'PF':>7} {'refusés':>8}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for scenario in SCENARIOS:
        if wanted is not None and scenario.name not in wanted:
            continue
        run = measure(dataset, scenario, module)
        runs[scenario.name] = run
        stats = run.aggregate()
        payload["scenarios"][scenario.name] = stats
        print(
            f"  {scenario.label:42} {stats['trades']:>6} {stats['win_rate']:>7.1%} "
            f"{stats['net_eur']:>9.2f} {stats['profit_factor'] or 0:>7.4f} "
            f"{stats['refused_entry_zone']:>8}"
        )
        if scenario.name == "baseline":
            payload["reference_check"] = _reference_check(stats)
            print(f"   -> {payload['reference_check']}")
        checkpoint()
        sys.stdout.flush()
        # Le verdict tout de suite après sa référence : sous charge, un run interrompu laisse
        # quand même la mesure complète de ce qu'il a déjà traversé.
        if not scenario.parity:
            detail = verdicts(run, dataset, module, run.costs)
            payload[f"verdicts_{scenario.name}"] = detail
            _print_verdicts(scenario.label, detail)
            checkpoint()
            sys.stdout.flush()

    for reference_name, gated_name in (
        ("baseline", "parity_paid"),
        ("baseline_prod_spread", "parity_paid_prod_spread"),
        ("baseline_prod_spread", "parity_production_prod_spread"),
        ("baseline_prod_spread", "parity_reference_prod_spread"),
        ("baseline_prod_spread", "parity_zone_0.2_prod_spread"),
        ("baseline_prod_spread", "parity_zone_0.3_prod_spread"),
    ):
        if reference_name in runs and gated_name in runs:
            payload[f"{gated_name}_vs_{reference_name}"] = suppression(
                runs[reference_name], runs[gated_name]
            )
    checkpoint()

    print(f"\nécrit : {args.output}")
    if "reference_check" in payload:
        print(payload["reference_check"])
    return 0


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def _print_verdicts(label: str, detail: Mapping[str, Any]) -> None:
    refused = detail["refused"]
    taken = detail["taken"]
    print(f"\n  -- {label} : verdict de la porte, entrée par entrée --")
    print(
        f"     entrées examinées      : {detail['trades_examined']} (non appariées "
        f"{detail['unmatched_signals']}, reconstructions incohérentes "
        f"{detail['reconstruction_mismatches']})"
    )
    print(
        f"     refusées               : {refused['count']} "
        f"({detail['refused_share_of_trades']:.1%}) — net {refused['net_eur']:+.2f} EUR, "
        f"réussite {refused['win_rate']:.1%}, PF {_fmt(refused['profit_factor'])}, "
        f"moyenne {refused['average_eur']:+.2f} EUR"
    )
    print(
        f"     acceptées              : {taken['count']} — net {taken['net_eur']:+.2f} EUR, "
        f"réussite {taken['win_rate']:.1%}, PF {_fmt(taken['profit_factor'])}, "
        f"moyenne {taken['average_eur']:+.2f} EUR"
    )
    print(
        f"     dépassement médian     : {detail['refused_overshoot_usd_median']:+.2f} USD "
        f"(max {detail['refused_overshoot_usd_max']:+.2f})"
    )
    print(
        f"     bande / spread (médian): {detail['band_over_spread_median']:.2f}x "
        f"pour un spread de {detail['spread_usd']:.4f} USD"
    )
    structural = detail["structural_rule"]
    print(
        f"     règle à l'instant du signal : {structural['refused']['count']} refus "
        f"({structural['share']:.1%})"
    )
    faithful = detail["production_basis"]
    print(
        f"     ask entier (fidèle)         : {faithful['refused']['count']} refus "
        f"({faithful['share']:.1%}), net {faithful['refused']['net_eur']:+.2f} EUR, "
        f"PF {_fmt(faithful['refused']['profit_factor'])}"
    )
    basis = detail["reference_basis"]
    print(
        f"     option B (bande décalée)    : {basis['refused']['count']} refus "
        f"({basis['share']:.1%}), net {basis['refused']['net_eur']:+.2f} EUR"
    )
    print("     courbe de largeur (entrées inchangées) :")
    for width, summary in detail["width_curve"].items():
        print(
            f"       {width} ATR -> {summary['count']:>4} gardées, net "
            f"{summary['net_eur']:>+9.2f} EUR, PF {_fmt(summary['profit_factor'])}"
        )


def _reference_check(stats: Mapping[str, Any]) -> str:
    gaps = []
    for key, expected in REFERENCE.items():
        actual = float(stats[key])
        tolerance = 0.02 * abs(expected) if key != "win_rate" else 0.01
        if abs(actual - expected) > tolerance:
            gaps.append(f"{key}: {actual:.4f} vs {expected}")
    return "référence : CONFORME" if not gaps else "référence : ÉCART — " + " ; ".join(gaps)


def _use_utf8_when_redirected() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and not stream.isatty():
            reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
