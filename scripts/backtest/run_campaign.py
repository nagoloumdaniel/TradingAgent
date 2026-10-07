"""TASK-064 / TASK-065 — run a reproducible research campaign and print its report.

Examples
--------
    uv run python scripts/backtest/run_campaign.py
    uv run python scripts/backtest/run_campaign.py --datasets docs/research/datasets

Without `--datasets`, seeded synthetic M15 series are used (no Deriv history is versioned
yet). With `--datasets`, every frozen `*.jsonl` dataset produced by TASK-060 is used as-is:
the campaign never cares whether the candles came from a generator or from the terminal.
The candidate manifest is written under `docs/research/candidates/`, never in
`config/strategies/`, and it loads with the production catalog loader.
"""

import argparse
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from tradingagent.backtest.costs import CostModel
from tradingagent.backtest.datasets import (
    CandleDataset,
    DatasetStore,
    SyntheticRegime,
    synthetic_dataset,
)
from tradingagent.backtest.harness import BacktestConfig
from tradingagent.backtest.randomness import DeterministicRandom
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.campaign import CampaignReport, CandidateSpec, run_campaign
from tradingagent.research.promotion import (
    AcceptanceThresholds,
    ManifestSpec,
    evidence_from_campaign,
    promote,
    write_thresholds,
)
from tradingagent.strategies.library.witness import Witness, WitnessParameters
from tradingagent.strategies.manifest import StrategyManifest
from tradingagent.strategies.registry import REGISTRY

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "docs" / "research"
MARKETS = ("frxXAUUSD", "cryBTCUSD", "cryETHUSD")
START_PRICES = {"frxXAUUSD": 2000.0, "cryBTCUSD": 60_000.0, "cryETHUSD": 3_000.0}
START = datetime(2026, 1, 5, tzinfo=UTC)
TIMEFRAME = Timeframe.M15
DEFAULT_BARS = 1_500
SYNTHETIC_SEED = 20_261_007
GOLD = "frxXAUUSD"


def witness_manifest(symbols: Sequence[str]) -> StrategyManifest:
    return StrategyManifest.model_validate(
        {
            "strategy_id": "witness",
            "version": "1.0.0",
            "max_mode": "SIGNAL",
            "allowed_symbols": list(symbols),
            "timeframes": [TIMEFRAME.value],
            "history_bars": 100,
            "expiry_bars": 2,
        }
    )


def witness_factory(parameters: Mapping[str, float]) -> Witness:
    return Witness(WitnessParameters(**parameters))


def candidate_specs(symbols: Sequence[str]) -> list[CandidateSpec]:
    def parameters(fast: int, slow: int, rr: float) -> dict[str, float]:
        return {
            "ema_fast": fast,
            "ema_slow": slow,
            "atr_period": 14,
            "stop_atr_multiplier": 1.5,
            "take_profit_rr": rr,
            "entry_zone_atr": 0.1,
        }

    manifest = witness_manifest(symbols)
    return [
        CandidateSpec("fast-1.5R", manifest, witness_factory, parameters(10, 30, 1.5)),
        CandidateSpec("balanced-2R", manifest, witness_factory, parameters(20, 50, 2.0)),
        CandidateSpec("slow-2.5R", manifest, witness_factory, parameters(30, 80, 2.5)),
    ]


def build_common_factor(bars: int) -> list[float]:
    stream = DeterministicRandom(SYNTHETIC_SEED)
    # A market-wide factor plus per-market idio noise: the campaign has to measure how
    # much of the crypto move is really the same risk as gold.
    return [stream.gauss() for _ in range(bars)]


def synthetic_markets(bars: int) -> dict[str, CandleDataset]:
    factor = build_common_factor(bars)
    # `beta` is the per-bar return loading on the unit factor, so the resulting
    # correlations are realistic: BTC and ETH share most of their risk, gold much less.
    betas = {"frxXAUUSD": 0.0006, "cryBTCUSD": 0.0025, "cryETHUSD": 0.0025}
    volatilities = {"frxXAUUSD": 0.0012, "cryBTCUSD": 0.0015, "cryETHUSD": 0.0012}
    drift = {"frxXAUUSD": 0.00002, "cryBTCUSD": 0.00004, "cryETHUSD": 0.00003}
    datasets: dict[str, CandleDataset] = {}
    for index, market in enumerate(MARKETS):
        regimes = (
            SyntheticRegime(bars=bars, drift=drift[market], volatility=volatilities[market]),
        )
        datasets[market] = synthetic_dataset(
            f"synthetic-m15-{bars}",
            market,
            TIMEFRAME,
            START,
            regimes,
            seed=SYNTHETIC_SEED + index,
            start_price=START_PRICES[market],
            common_returns=factor,
            beta=betas[market],
            decimals=2,
        )
    return datasets


def load_markets(directory: Path) -> dict[str, CandleDataset]:
    store = DatasetStore(directory)
    found = store.load_all()
    if not found:
        raise SystemExit(f"no *.jsonl dataset found in {directory}")
    return {dataset.symbol: dataset for dataset in found.values()}


def config_for(market: str, dataset: CandleDataset) -> BacktestConfig:
    price = dataset.candles[0].close
    costs = CostModel(
        spread=round(price * 0.00005, 6),
        slippage_fixed=round(price * 0.00002, 6),
        commission_per_trade=Decimal("0.5"),
    )
    return BacktestConfig(
        symbol=market,
        costs=costs,
        mode=TradingMode.SIGNAL,
        max_concurrent_positions=1,
    )


def campaign_to_dict(
    report: CampaignReport, datasets: Mapping[str, CandleDataset]
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "datasets": {
            market: {
                "dataset_id": dataset.dataset_id,
                "fingerprint": dataset.fingerprint,
                "source": dataset.source,
                "bars": dataset.bars,
            }
            for market, dataset in sorted(datasets.items())
        },
        "markets": [
            {
                "market": market.market,
                "dataset_id": market.dataset_id,
                "selected": market.selected,
                "selection_basis": market.selection_basis,
                "holdout_still_sealed": market.holdout_still_sealed,
                "candidates": [
                    {
                        "label": candidate.label,
                        "selected": candidate.selected,
                        "stability_score": candidate.stability_score,
                        "fragile": candidate.fragile,
                        "reasons": list(candidate.reasons),
                        "train_net_profit": str(candidate.train.net_profit),
                        "validation_net_profit": str(candidate.validation.net_profit),
                        "cost_net_profit": str(candidate.cost_net.net_profit),
                        "cost_net_profit_factor": candidate.cost_net.profit_factor,
                        "parameters": {
                            key: float(value) for key, value in candidate.parameters.items()
                        },
                    }
                    for candidate in market.candidates
                ],
            }
            for market in report.markets
        ],
        "correlations": [
            {
                "markets": [pair.market_a, pair.market_b],
                "correlation": pair.correlation,
                "aligned_bars": pair.aligned_bars,
                "high": pair.high,
            }
            for pair in report.correlations
        ],
    }


def print_report(report: CampaignReport, datasets: Mapping[str, CandleDataset]) -> None:
    print("== Datasets ==")
    for market, dataset in sorted(datasets.items()):
        print(
            f"  {market:12s} {dataset.dataset_id:22s} {dataset.bars:5d} bars "
            f"source={dataset.source} fingerprint={dataset.fingerprint[:12]}…"
        )
    print("== Markets (selection is on robustness, never on net profit) ==")
    for market in report.markets:
        print(f"  {market.market}: selected {market.selected!r} — {market.selection_basis}")
        print(f"    holdout still sealed: {market.holdout_still_sealed}")
        for candidate in market.candidates:
            marker = "*" if candidate.selected else " "
            print(
                f"   {marker} {candidate.label:14s} stability={candidate.stability_score:.3f} "
                f"train={candidate.train.net_profit:>9} val={candidate.validation.net_profit:>9} "
                f"cost_net={candidate.cost_net.net_profit:>9} "
                f"PF={candidate.cost_net.profit_factor} fragile={candidate.fragile}"
            )
            for reason in candidate.reasons:
                print(f"       - {reason}")
    print("== Correlations (Pearson of aligned M15 returns) ==")
    for pair in report.correlations:
        value = "n/a" if pair.correlation is None else f"{pair.correlation:+.3f}"
        flag = "HIGH" if pair.high else "ok"
        print(
            f"  {pair.market_a} / {pair.market_b}: {value} over {pair.aligned_bars} bars [{flag}]"
        )


def promotion_gate(report: CampaignReport, output: Path) -> None:
    """TASK-065: thresholds are written first, then the decision, then the manifest."""
    gold = next(
        (market for market in report.markets if market.market == GOLD),
        next((market for market in report.markets if market.selected is not None), None),
    )
    if gold is None or gold.selected is None:
        print("== Promotion ==")
        print("  no selected candidate on gold, nothing to promote")
        return
    candidate = next(item for item in gold.candidates if item.selected)
    stability = candidate.stability_report
    if stability is None:
        raise SystemExit("selected candidate carries no stability report")
    thresholds = AcceptanceThresholds(
        version=f"synthetic-{SYNTHETIC_SEED}",
        min_trades=30,
        min_stability_score=0.5,
    )
    thresholds_path = write_thresholds(output / "thresholds.json", thresholds)
    correlation = next(
        (
            pair.correlation
            for pair in report.correlations
            if GOLD in (pair.market_a, pair.market_b)
        ),
        None,
    )
    evidence = evidence_from_campaign(
        "witness@1.0.0",
        dict(candidate.parameters),
        candidate.train,
        candidate.validation,
        candidate.cost_net,
        stability,
        thresholds,
        correlation_with_existing=correlation,
    )
    spec = ManifestSpec(
        strategy_id="witness",
        version="1.1.0",
        allowed_symbols=tuple(sorted(datasets_markets(report))),
        timeframes=(TIMEFRAME,),
        history_bars=100,
        parameters=dict(candidate.parameters),
    )
    outcome = promote(
        evidence,
        thresholds,
        spec,
        manifest_dir=output / "candidates",
        decision_dir=output / "decisions",
        decided_at=datetime.now(UTC),
        decided_by="research-campaign",
    )
    print("== Promotion ==")
    print(f"  thresholds: {thresholds_path} (digest {thresholds.digest[:12]}…)")
    print(f"  candidate {candidate.label} promoted: {outcome.decision.promoted}")
    for reason in outcome.decision.reasons:
        print(f"    - {reason}")
    print(f"  decision: {outcome.decision_path}")
    print(f"  candidate manifest: {outcome.manifest_path}")


def datasets_markets(report: CampaignReport) -> list[str]:
    return [market.market for market in report.markets]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--datasets",
        type=Path,
        default=None,
        help="directory of frozen *.jsonl datasets; synthetic seeded data is used otherwise",
    )
    parser.add_argument("--bars", type=int, default=DEFAULT_BARS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if args.datasets is not None:
        datasets = load_markets(args.datasets)
        print(f"loaded {len(datasets)} frozen dataset(s) from {args.datasets}")
    else:
        datasets = synthetic_markets(args.bars)
        print(f"no dataset directory given: generated {len(datasets)} seeded synthetic series")

    report = run_campaign(
        datasets,
        candidate_specs(list(datasets)),
        config_for=config_for,
    )
    print_report(report, datasets)

    args.output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    json_path = args.output / f"{stamp}-campaign.json"
    json_path.write_text(
        json.dumps(campaign_to_dict(report, datasets), indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"report written to {json_path}")
    if datasets:
        promotion_gate(report, args.output)
    print(f"production manifests would be loaded by REGISTRY: {sorted(REGISTRY)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
