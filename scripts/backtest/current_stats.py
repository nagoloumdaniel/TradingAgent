"""The backtest statistics of the strategies the agent actually runs, market by market.

Why this script exists next to `run_campaign.py`: the campaign compares *candidates*
(fast-1.5R, balanced-2R, slow-2.5R) on frozen series. The question here is different — "where
does what the agent executes today stand?" — so the measured version is the one
`config/agent.yaml` designates, with the parameters read from its own manifest, on the
timeframe that manifest declares, charged with the repository's cost model.

Four things are therefore *resolved*, never assumed, and each one is printed with its value:

* the version — `config/agent.yaml` is the file the agent loads at start-up (`deployed_refs`);
* the parameters — read from `config/strategies/<ref>.yaml`, not restated here;
* the timeframe — the manifest declares it, and the frozen dataset must be the same one, or
  the measurement stops rather than answering a different question;
* the costs — identical to `run_campaign.py` and `improve.py`: spread at 0.5 bp of the first
  close, slippage at 0.2 bp, 0.50 EUR per trade, `mode=SIGNAL`, one position at a time.

Read-only on production: nothing is promoted, nothing is written under `config/strategies/`.
The report lands in `docs/research/stats/`, one JSON per market, and on standard output.

    uv run python scripts/backtest/current_stats.py
    uv run python scripts/backtest/current_stats.py --market XAUUSD
    uv run python scripts/backtest/current_stats.py --bars 800     # smoke test, truncated

The sealed holdout is **not** unlocked: `confirm_holdout` stays off. Reading a holdout is a
deliberate act that spends it, and it is not a side effect of asking for a report.
"""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from tradingagent.analytics.model import Performance
from tradingagent.backtest.costs import campaign_costs
from tradingagent.backtest.datasets import CandleDataset, DatasetStore
from tradingagent.backtest.harness import BacktestConfig
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.campaign import (
    CandidateReport,
    CandidateSpec,
    MarketReport,
    run_campaign,
)
from tradingagent.strategies.manifest import StrategyManifest
from tradingagent.strategies.registry import REGISTRY

ROOT = Path(__file__).resolve().parents[2]
AGENT_CONFIG = ROOT / "config" / "agent.yaml"
STRATEGY_DIR = ROOT / "config" / "strategies"
DATASET_DIR = ROOT / "docs" / "research" / "datasets"
OUTPUT_DIR = ROOT / "docs" / "research" / "stats"


def read_manifest(path: Path) -> Mapping[str, Any]:
    """A production manifest, as written. Same tiny YAML subset as the other scripts."""
    import yaml

    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def deployed_refs(path: Path = AGENT_CONFIG) -> dict[str, str]:
    """What the agent loads for each enabled market, as `config/agent.yaml` declares it."""
    document = read_manifest(path)
    found: dict[str, str] = {}
    for market in document.get("markets", []):
        if not isinstance(market, Mapping) or not market.get("enabled", True):
            continue
        symbol = market.get("symbol")
        strategy = market.get("strategy")
        if symbol and strategy:
            found[str(symbol)] = str(strategy)
    return found


def spec_for(market: str, ref: str) -> CandidateSpec:
    """The deployed version, as a campaign candidate, built the way production builds it.

    The class comes from code (`REGISTRY`), the values from the manifest: a file is never
    allowed to name what runs.
    """
    document = read_manifest(STRATEGY_DIR / f"{ref}.yaml")
    manifest = StrategyManifest.model_validate(document)
    builder = REGISTRY.get(manifest.strategy_id)
    if builder is None:
        raise SystemExit(f"{ref}: {manifest.strategy_id!r} is not a runnable strategy")
    parameters = {
        key: value
        for key, value in document["parameters"].items()
        if isinstance(value, int | float)
    }
    return CandidateSpec(
        label=ref,
        manifest=manifest,
        factory=lambda values: builder(builder.parameters_model(**values)),
        parameters=parameters,
    )


def config_for(market: str, dataset: CandleDataset) -> BacktestConfig:
    """The repository's charged cost model — the same numbers as `run_campaign.py`."""
    price = dataset.candles[0].close
    return BacktestConfig(
        symbol=market,
        costs=campaign_costs(price),
        mode=TradingMode.SIGNAL,
        max_concurrent_positions=1,
    )


def number(value: Any) -> Any:
    """JSON-safe: Decimal and timedelta do not serialise, and a rounded float is enough."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | Timeframe):
        return str(value)
    if hasattr(value, "total_seconds"):
        return round(float(value.total_seconds()), 3)
    if isinstance(value, float):
        return round(value, 6)
    return value


def performance_stats(performance: Performance) -> dict[str, Any]:
    """Every figure of `analytics.Performance`, under its own name."""
    return {
        "trades": performance.trades,
        "wins": performance.wins,
        "losses": performance.losses,
        "win_rate": number(performance.win_rate),
        "gross_profit": number(performance.gross_profit),
        "gross_loss": number(performance.gross_loss),
        "net_profit": number(performance.net_profit),
        "profit_factor": number(performance.profit_factor),
        "expectancy": number(performance.expectancy),
        "average_win": number(performance.average_win),
        "average_loss": number(performance.average_loss),
        "best": number(performance.best),
        "worst": number(performance.worst),
        "max_drawdown": number(performance.max_drawdown),
        "max_drawdown_duration_s": number(performance.max_drawdown_duration),
        "max_win_streak": performance.max_win_streak,
        "max_loss_streak": performance.max_loss_streak,
        "realized_rr": number(performance.realized_rr),
        "average_position_duration_s": number(performance.average_position_duration),
        "average_slippage": number(performance.average_slippage),
        "average_spread": number(performance.average_spread),
        "sharpe": number(performance.sharpe),
        "sortino": number(performance.sortino),
        "insufficient_sample": performance.insufficient_sample,
    }


def candidate_stats(report: CandidateReport) -> dict[str, Any]:
    """A measured version: its windows, its robustness, and the gates that were evaluated."""
    stability = report.stability_report
    stats: dict[str, Any] = {
        "label": report.label,
        "selected": report.selected,
        "fragile": report.fragile,
        "reasons": list(report.reasons),
        "stability_score": number(report.stability_score),
        "train": performance_stats(report.train),
        "validation": performance_stats(report.validation),
        "cost_net": performance_stats(report.cost_net),
        "stability": (
            None
            if stability is None
            else {
                "score": number(stability.score),
                "out_of_sample_retention": number(stability.out_of_sample_retention),
                "parameter_dispersion": number(stability.parameter_dispersion),
                "profitable_regime_ratio": number(stability.profitable_regime_ratio),
                "trades": stability.trades,
                "reasons": list(stability.reasons),
            }
        ),
    }
    gates = report.gates
    if gates is not None:
        folds = gates.walk_forward
        stats["gates"] = {
            "walk_forward_folds": f"{folds.profitable_folds}/{folds.folds}",
            "monte_carlo_probability_of_profit": number(gates.probability_of_profit),
            "p_value": number(gates.p_value),
            "significant_after_correction": gates.significant,
            "profit_factor_at_double_costs": number(gates.stressed.profit_factor),
            "out_of_sample": (
                None if gates.out_of_sample is None else performance_stats(gates.out_of_sample)
            ),
        }
    stats["gate_verdicts"] = [
        {
            "stage": verdict.stage.value,
            "status": verdict.status.value,
            "passed": verdict.passed,
            "evaluable": verdict.evaluable,
            "evidence": {key: number(value) for key, value in verdict.evidence.items()},
            "threshold": number(verdict.threshold),
            "reason": verdict.reason,
            "evaluator": verdict.evaluator,
        }
        for verdict in report.gate_verdicts
    ]
    return stats


def market_block(
    market: str, ref: str, dataset: CandleDataset, report: MarketReport
) -> dict[str, Any]:
    """One market, entirely: what was measured, on what, and what came out."""
    document = read_manifest(STRATEGY_DIR / f"{ref}.yaml")
    measured = next(item for item in report.candidates if item.label == ref)
    return {
        "market": market,
        "ref": ref,
        "strategy_id": document["strategy_id"],
        "version": document["version"],
        "max_mode": document["max_mode"],
        "timeframe": dataset.timeframe.value,
        "dataset_id": dataset.dataset_id,
        "dataset_fingerprint": dataset.fingerprint,
        "bars": len(dataset.candles),
        "window_start": dataset.candles[0].open_time.isoformat(),
        "window_end": dataset.candles[-1].open_time.isoformat(),
        "parameters": {key: number(value) for key, value in document["parameters"].items()},
        "costs": {
            "spread_0_5bp_of_first_close": number(config_for(market, dataset).costs.spread),
            "slippage_0_2bp_of_first_close": number(
                config_for(market, dataset).costs.slippage_fixed
            ),
            "commission_per_trade_eur": 0.5,
            "mode": TradingMode.SIGNAL.value,
            "max_concurrent_positions": 1,
        },
        "result": candidate_stats(measured),
    }


def line(label: str, value: Any) -> str:
    if isinstance(value, float):
        return f"  {label:<34} {value:,.2f}".replace(",", " ")
    return f"  {label:<34} {value}"


def as_percent(value: float | None) -> str:
    """`analytics.Performance` stores ratios in 0..1; a report must not read as 0.38 %."""
    if value is None:
        return "non défini"
    return f"{value * 100:.1f} %"


def print_block(block: Mapping[str, Any]) -> None:
    """The operator's view: identity first, then the three windows, then the robustness."""
    result = block["result"]
    print(f"== {block['market']} — {block['ref']} ({block['timeframe']}) ==")
    start, end = block["window_start"][:16], block["window_end"][:16]
    print(f"  {block['bars']} bougies gelées, {start} → {end} UTC")
    print(f"  jeu : {block['dataset_id']}")
    print(f"  paramètres : {block['parameters']}")
    print()
    for window in ("train", "validation", "cost_net"):
        stats = result[window]
        print(f"  -- {window} " + "-" * (44 - len(window)))
        print(line("opérations", stats["trades"]))
        print(line("gagnantes / perdantes", f"{stats['wins']} / {stats['losses']}"))
        print(line("taux de réussite", as_percent(stats["win_rate"])))
        print(line("profit net (EUR)", stats["net_profit"]))
        print(line("facteur de profit", stats["profit_factor"]))
        print(line("espérance par opération (EUR)", stats["expectancy"]))
        print(
            line("gain moyen / perte moyenne", f"{stats['average_win']} / {stats['average_loss']}")
        )
        print(line("meilleure / pire opération", f"{stats['best']} / {stats['worst']}"))
        print(line("drawdown maximal (EUR)", stats["max_drawdown"]))
        print(line("plus longue série perdante", stats["max_loss_streak"]))
        print(line("ratio rendement/risque réalisé", stats["realized_rr"]))
        print(line("spread moyen payé (prix)", stats["average_spread"]))
        print(line("Sharpe / Sortino", f"{stats['sharpe']} / {stats['sortino']}"))
        print(line("échantillon insuffisant", stats["insufficient_sample"]))
        print()
    stability = result["stability"]
    print("  -- robustesse " + "-" * 32)
    print(line("score de stabilité", result["stability_score"]))
    if stability is not None:
        print(line("rétention hors échantillon", stability["out_of_sample_retention"]))
        print(line("dispersion des paramètres", stability["parameter_dispersion"]))
        print(line("périodes rentables", as_percent(stability["profitable_regime_ratio"])))
    gates = result.get("gates")
    if gates is not None:
        print(line("plis walk-forward rentables", gates["walk_forward_folds"]))
        print(line("Monte-Carlo P(profit)", gates["monte_carlo_probability_of_profit"]))
        print(line("p-value", gates["p_value"]))
        print(line("facteur de profit à 2x coûts", gates["profit_factor_at_double_costs"]))
    if result["reasons"]:
        print(f"  fragilités : {' ; '.join(result['reasons'])}")
    print()
    passed = [verdict for verdict in result["gate_verdicts"] if verdict["passed"]]
    refused = [
        verdict
        for verdict in result["gate_verdicts"]
        if verdict["evaluable"] and not verdict["passed"]
    ]
    absent = [verdict for verdict in result["gate_verdicts"] if not verdict["evaluable"]]
    print(
        f"  portes : {len(passed)} franchie(s), {len(refused)} refusée(s), "
        f"{len(absent)} non évaluable(s) par un backtest"
    )
    for verdict in result["gate_verdicts"]:
        if verdict["passed"]:
            mark = "ok "
        elif verdict["evaluable"]:
            mark = "NON"
        else:
            mark = "-- "
        figure = ", ".join(f"{key}={value}" for key, value in list(verdict["evidence"].items())[:2])
        threshold = "—" if verdict["threshold"] is None else verdict["threshold"]
        print(f"    [{mark}] {verdict['stage']:<22} {figure:<40} (seuil {threshold})")
    print()


def _use_utf8_when_redirected() -> None:
    """A redirected Windows pipe defaults to a legacy code page, which cannot encode every
    French accent or an arrow. Ask for UTF-8 instead of crashing once the report is written."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and not stream.isatty():
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _use_utf8_when_redirected()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", type=Path, default=DATASET_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--market", default=None, help="ne mesurer qu'un marché (ex. XAUUSD)")
    parser.add_argument(
        "--bars",
        type=int,
        default=0,
        help="ne garder que les N dernières bougies (vérification rapide, jeu tronqué)",
    )
    parser.add_argument("--no-write", action="store_true", help="afficher sans écrire de JSON")
    args = parser.parse_args(argv)

    datasets = DatasetStore(args.datasets).load_all()
    if not datasets:
        raise SystemExit(f"no frozen dataset in {args.datasets}")
    refs = deployed_refs()
    markets = [args.market] if args.market else sorted(datasets)
    if args.market and args.market not in datasets:
        raise SystemExit(
            f"{args.market}: aucun jeu gelé (disponibles : {', '.join(sorted(datasets))})"
        )

    blocks: list[dict[str, Any]] = []
    for market in markets:
        ref = refs.get(market)
        if ref is None:
            print(f"== {market} : absent de {AGENT_CONFIG.name}, rien à mesurer ==")
            continue
        dataset = datasets[market]
        declared = read_manifest(STRATEGY_DIR / f"{ref}.yaml")["timeframes"]
        if dataset.timeframe.value not in declared:
            raise SystemExit(
                f"{market}: le jeu gelé est en {dataset.timeframe.value} alors que {ref} déclare "
                f"{declared}: mesurer une autre unité de temps répondrait à une autre question"
            )
        if args.bars > 0:
            # Truncated on purpose, and identified as such: the fingerprint is derived from
            # the candles, so it changes with them and nothing here can be mistaken for the
            # frozen series it came from.
            dataset = replace(
                dataset,
                dataset_id=f"{dataset.dataset_id}-tronque-{args.bars}",
                candles=dataset.candles[-args.bars :],
            )
            print(
                f"ATTENTION : {market} tronqué aux {args.bars} dernières bougies — "
                "vérification de plomberie, pas une mesure"
            )
        report = run_campaign({market: dataset}, [spec_for(market, ref)], config_for=config_for)
        block = market_block(market, ref, dataset, report.markets[0])
        blocks.append(block)

    for block in blocks:
        print_block(block)

    if not args.no_write and blocks:
        args.output.mkdir(parents=True, exist_ok=True)
        for block in blocks:
            path = args.output / f"{block['market']}-{block['ref']}.json"
            payload = json.dumps(block, indent=2, ensure_ascii=False) + "\n"
            path.write_text(payload, encoding="utf-8")
            print(f"écrit : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
