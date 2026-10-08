"""TASK-064 / TASK-066 — run a reproducible research campaign and print its report.

Examples
--------
    uv run python scripts/backtest/run_campaign.py
    uv run python scripts/backtest/run_campaign.py --datasets docs/research/datasets

Without `--datasets`, seeded synthetic M15 series are used (no Deriv history is versioned
yet). With `--datasets`, every frozen `*.jsonl` dataset produced by TASK-060 is used as-is:
the campaign never cares whether the candles came from a generator or from the terminal.
The candidate manifest is written under `docs/research/candidates/`, never in
`config/strategies/`, and it loads with the production catalog loader.

TASK-066 prints the **nine** promotion gates of §49, one line each, with the figure, the
function that produced it and the threshold it was compared against. Two of the nine -- paper
trading and the risk engine -- cannot be produced by a backtest on a frozen history, and the
report says so out loud: a gate reported as passed without proof would be worse than a gate
reported as open. The Monte-Carlo probability of profit is measured for every candidate and
handed to `promotion.evidence_from_campaign`, so the promotion control has a number to check
instead of quietly skipping the check.
"""

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
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
from tradingagent.core.states import ValidationStage
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.campaign import (
    DEFAULT_WALK_FORWARD_PLAN,
    STRESS_COST_MULTIPLIER,
    CampaignReport,
    CandidateReport,
    CandidateSpec,
    MarketReport,
    run_campaign,
)
from tradingagent.research.promotion import (
    AcceptanceThresholds,
    ManifestSpec,
    PromotionEvidence,
    PromotionOutcome,
    evaluate_promotion,
    evidence_from_campaign,
    promote,
    record_decision,
    write_thresholds,
)
from tradingagent.research.protocol import DataWindow, GateStatus, GateVerdict
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
#: The false-discovery rate the campaign's own selection is priced at. It is a reporting
#: statement, never a threshold a candidate has to clear: clearing it is what the correction
#: decides, and the campaign prints the number it used.
DEFAULT_FALSE_DISCOVERY_RATE = 0.10
MONTE_CARLO_ITERATIONS = 1_000
#: The campaign cuts its tape with the **anchored** split: the newest validation and sealed
#: windows stay where they are when older history is prepended, and the training window grows.
#: Without it, extending a fetch slides every window backwards and the resulting before/after
#: comparison reads two different market periods.
ANCHOR_SPLIT = True
TRAIN_FRACTION = 0.6
VALIDATION_FRACTION = 0.2


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


def candidate_to_dict(candidate: CandidateReport) -> dict[str, Any]:
    gates = candidate.gates
    return {
        "label": candidate.label,
        "selected": candidate.selected,
        "stability_score": candidate.stability_score,
        "fragile": candidate.fragile,
        "reasons": list(candidate.reasons),
        "train_net_profit": str(candidate.train.net_profit),
        "validation_net_profit": str(candidate.validation.net_profit),
        # The charged run, once: the figure the `costs` gate is decided on. Its stressed
        # counterpart is published under `stressed`, with the multiplier that produced it.
        "cost_net_profit": str(candidate.cost_net.net_profit),
        "cost_net_profit_factor": candidate.cost_net.profit_factor,
        "cost_net_cost_multiplier": 1.0,
        "parameters": {key: float(value) for key, value in candidate.parameters.items()},
        "walk_forward": (
            None
            if gates is None
            else {
                "folds": gates.walk_forward.folds,
                "profitable_folds": gates.walk_forward.profitable_folds,
                "ratio": gates.walk_forward.ratio,
                "trades": gates.walk_forward.trades,
                "net_profit": str(gates.walk_forward.net_profit),
            }
        ),
        "monte_carlo": (
            None
            if gates is None
            else {
                "probability_of_profit": gates.probability_of_profit,
                "p_value": gates.p_value,
                "iterations": gates.monte_carlo.iterations,
                "trades": gates.monte_carlo.trades,
                "net_profit_p05": gates.monte_carlo.net_profit_p05,
                "net_profit_median": gates.monte_carlo.net_profit_median,
                "net_profit_p95": gates.monte_carlo.net_profit_p95,
                "worst_max_drawdown": gates.monte_carlo.worst_max_drawdown,
            }
        ),
        "stressed": (
            None
            if gates is None
            else {
                "cost_multiplier": STRESS_COST_MULTIPLIER,
                "net_profit": str(gates.stressed.net_profit),
                "profit_factor": gates.stressed.profit_factor,
                "trades": gates.stressed.trades,
            }
        ),
        "out_of_sample": (
            None
            if gates is None or gates.out_of_sample is None
            else {
                "net_profit": str(gates.out_of_sample.net_profit),
                "profit_factor": gates.out_of_sample.profit_factor,
                "trades": gates.out_of_sample.trades,
            }
        ),
        # `false` is the honest value for "the correction did not let this candidate claim a
        # discovery"; it is not a threshold the candidate failed by itself.
        "false_discovery_significant": None if gates is None else gates.significant,
        "passing_gates": [stage.value for stage in candidate.passing_stages()],
        "gates": [gate_to_dict(item) for item in candidate.gate_verdicts],
    }


def gate_to_dict(verdict: GateVerdict) -> dict[str, Any]:
    return {
        "stage": verdict.stage.value,
        "status": verdict.status.value,
        "evaluated": verdict.evaluable,
        "passed": verdict.passed,
        "evaluator": verdict.evaluator,
        "reason": verdict.reason,
        "threshold": verdict.threshold,
        "evidence": verdict.evidence,
    }


def window_to_dict(window: DataWindow) -> dict[str, Any]:
    return window.to_dict()


def market_to_dict(market: MarketReport) -> dict[str, Any]:
    return {
        "market": market.market,
        "dataset_id": market.dataset_id,
        "selected": market.selected,
        "selection_basis": market.selection_basis,
        "holdout_still_sealed": market.holdout_still_sealed,
        "holdout_unlocks": market.holdout_unlocks,
        "out_of_sample_net_profit": (
            None if market.out_of_sample is None else str(market.out_of_sample.net_profit)
        ),
        # The periods actually measured, published so a before/after comparison can prove the
        # newest windows did not move -- a comparison that cannot name them is not controlled.
        "anchored": market.anchored,
        "dataset_window": (
            None if market.dataset_window is None else window_to_dict(market.dataset_window)
        ),
        "split_windows": [window_to_dict(window) for window in market.split_windows],
        "passing_gates": [stage.value for stage in market.passing_stages()],
        "gates": [gate_to_dict(item) for item in market.gate_verdicts],
        "candidates": [candidate_to_dict(candidate) for candidate in market.candidates],
    }


def campaign_to_dict(
    report: CampaignReport, datasets: Mapping[str, CandleDataset], thresholds: AcceptanceThresholds
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
        "thresholds": {
            "version": thresholds.version,
            "digest": thresholds.digest,
            "document": thresholds.to_dict(),
        },
        "gate_protocol": {
            "walk_forward": {
                "train_bars": DEFAULT_WALK_FORWARD_PLAN.train_bars,
                "validation_bars": DEFAULT_WALK_FORWARD_PLAN.validation_bars,
                "step_bars": DEFAULT_WALK_FORWARD_PLAN.step_bars,
                # None means "no ceiling": the rolling origin walks to the end of the rolling
                # window, so the fold count follows the history instead of being a constant.
                "max_folds": DEFAULT_WALK_FORWARD_PLAN.max_folds,
                "fold_ceiling": (
                    "none: every fold that fits in the rolling window is played"
                    if DEFAULT_WALK_FORWARD_PLAN.max_folds is None
                    else f"at most {DEFAULT_WALK_FORWARD_PLAN.max_folds} folds"
                ),
            },
            "split": {
                "anchored": ANCHOR_SPLIT,
                "train_fraction": TRAIN_FRACTION,
                "validation_fraction": VALIDATION_FRACTION,
            },
            "monte_carlo_iterations": MONTE_CARLO_ITERATIONS,
            "stress_cost_multiplier": STRESS_COST_MULTIPLIER,
            # The two cost gates read two different runs, and the report has to say which is
            # which: they used to publish one figure twice, and the "net profit factor" was
            # the one measured at twice the charged costs.
            "cost_measurement": (
                "the costs gate reads the validation window under the charged cost model (1x); "
                "the stress gate reads the same window with those costs doubled (2x)"
            ),
            "false_discovery_rate": DEFAULT_FALSE_DISCOVERY_RATE,
            "p_value_measurement": (
                "validation window, measured for every candidate before any holdout is opened"
            ),
            "out_of_sample_measurement": (
                "sealed holdout, unlocked once per market for the selected candidate"
            ),
        },
        "gates": [gate_to_dict(item) for item in report.gate_verdicts],
        "passing_gates": [stage.value for stage in report.passing_stages()],
        "multiple_testing": (
            None
            if report.multiple_testing is None
            else {
                "method": report.multiple_testing.method,
                "alpha": report.multiple_testing.alpha,
                "hypotheses": report.multiple_testing.hypotheses,
                "discoveries_before": report.multiple_testing.discoveries_before,
                "discoveries_after": report.multiple_testing.discoveries_after,
                "rejected_by_correction": report.multiple_testing.rejected_by_correction,
                "bonferroni_threshold": report.multiple_testing.bonferroni_threshold,
                "expected_false_discoveries": (report.multiple_testing.expected_false_discoveries),
                "hypotheses_detail": [
                    {"label": label, "p_value": p_value} for label, p_value in report.hypotheses
                ],
            }
        ),
        "markets": [market_to_dict(market) for market in report.markets],
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
        print(
            f"    holdout still sealed: {market.holdout_still_sealed} "
            f"(unlocks {market.holdout_unlocks})"
        )
        for candidate in market.candidates:
            marker = "*" if candidate.selected else " "
            gates = candidate.gates
            probability = "n/a" if gates is None else f"{gates.probability_of_profit:.2f}"
            p_value = "n/a" if gates is None else f"{gates.p_value:.4f}"
            stressed_factor = None if gates is None else gates.stressed.profit_factor
            if gates is None:
                folds = "n/a"
            else:
                folds = f"{gates.walk_forward.profitable_folds}/{gates.walk_forward.folds}"
            print(
                f"   {marker} {candidate.label:14s} stability={candidate.stability_score:.3f} "
                f"train={candidate.train.net_profit:>9} val={candidate.validation.net_profit:>9} "
                f"cost_net(1x)={candidate.cost_net.net_profit:>9} "
                f"PF(1x)={candidate.cost_net.profit_factor} fragile={candidate.fragile}"
            )
            print(
                f"       walk-forward folds {folds} · Monte-Carlo P(profit)={probability} "
                f"p={p_value} · PF(2x)={stressed_factor}"
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


def print_windows(report: CampaignReport) -> None:
    """The periods each market actually measured, so a comparison can be held to them.

    A before/after comparison that cannot show its windows is comparing two market regimes,
    not one regime with more data. The split is anchored, which is what keeps the validation
    and sealed windows in place while the training window grows.
    """
    print("== Windows measured (anchored split: the newest windows do not move) ==")
    for market in report.markets:
        split = {window.name: window for window in market.split_windows}
        dataset = market.dataset_window
        source = ""
        if dataset is not None:
            source = (
                f"{dataset.bars:6d} bars {dataset.start.isoformat()} -> {dataset.end.isoformat()}"
            )
        print(f"  {market.market}: tape {source}")
        for name in ("train", "validation", "holdout"):
            window = split.get(name)
            if window is None:
                print(f"    {name:11s} (not reported)")
                continue
            print(
                f"    {name:11s} {window.bars:6d} bars "
                f"{window.start.isoformat()} -> {window.end.isoformat()}"
            )
        winner = market.winner
        if winner is not None and winner.gates is not None:
            outcome = winner.gates.walk_forward
            print(
                f"    walk-forward {outcome.folds} fold(s) played, "
                f"{outcome.skipped_folds} skipped; the last one is the newest available"
            )


def print_gates(report: CampaignReport) -> None:
    """The nine gates of §49, one line each: status, figure, threshold, evaluator, reason."""
    print("== Promotion gates (§49): the nine, evaluated or explicitly not ==")
    print(f"  {'GATE':22s} {'STATUS':13s} {'FIGURE':>12s} {'THRESHOLD':>10s}  EVALUATED BY")
    for verdict in report.gate_verdicts:
        figure = _figure(verdict)
        threshold = "—" if verdict.threshold is None else f"{verdict.threshold:g}"
        status = verdict.status.value
        print(
            f"  {verdict.stage.value:22s} {status:13s} {figure:>12s} {threshold:>10s}  "
            f"{verdict.evaluator}"
        )
        print(f"      {verdict.reason}")
    counted = _count_statuses(report)
    print("  totals: " + ", ".join(f"{status.value}={count}" for status, count in counted.items()))
    undecided = [item.stage.value for item in report.gate_verdicts if not item.evaluable]
    if undecided:
        print(
            "  not evaluable here: "
            + ", ".join(undecided)
            + " — a backtest on a frozen history cannot produce that evidence"
        )
    multiple = report.multiple_testing
    if multiple is not None:
        print("== Multiple testing: what the selection cost ==")
        print(
            f"  {multiple.method}, alpha={multiple.alpha:.2f}, hypotheses={multiple.hypotheses} "
            f"(one per candidate and market)"
        )
        print(
            f"  survivors before correction {multiple.discoveries_before} -> after "
            f"{multiple.discoveries_after} ({multiple.rejected_by_correction} demoted)"
        )
        print(
            f"  Bonferroni threshold for comparison: {multiple.bonferroni_threshold:.6f}; "
            f"expected false discoveries {multiple.expected_false_discoveries:.2f}"
        )
        for label, p_value in report.hypotheses:
            shown = "no p-value" if p_value is None else f"p={p_value:.4f}"
            print(f"    {label:28s} {shown}")
        claims = report.claims()
        if claims:
            print(
                "  candidates that cleared the correction (a promotion may only rest on one "
                "of these):"
            )
            for candidate in claims:
                assert candidate.gates is not None  # noqa: S101 - claims() guarantees it
                print(f"    {candidate.market}:{candidate.label} p={candidate.gates.p_value:.4f}")
        else:
            print(
                "  no candidate cleared the correction: the selection found nothing that "
                "stands out from the number of attempts, so no gate can lead to a promotion"
            )


def _figure(verdict: GateVerdict) -> str:
    """The figure a gate was decided on, taken from the candidates that decided it.

    A campaign verdict aggregates one selected candidate per market, so the table shows the
    smallest of those readings -- the one that decided a failing gate -- while the
    per-candidate figures stay in the JSON, where every market can be read at once.
    """
    evidence = verdict.evidence
    if "in_sample_trades" in evidence:
        return f"{evidence['in_sample_trades']} trades"
    if "probability_of_profit" in evidence:
        return f"{evidence['probability_of_profit']:.2f} P(profit)"
    markets = evidence.get("markets")
    if isinstance(markets, Mapping):
        figures = [item for item in markets.values() if isinstance(item, Mapping)]
        if figures:
            return _worst(verdict.stage, figures)
    if "figure" in evidence:
        value = evidence["figure"]
        return f"{value:.2f}" if isinstance(value, float) else str(value)
    return "not measured"


def _worst(stage: ValidationStage, figures: Sequence[Mapping[str, Any]]) -> str:
    """The most pessimistic reading of one gate across the markets of the campaign."""
    if stage is ValidationStage.BACKTEST:
        return f"{min(int(item['in_sample_trades']) for item in figures)} trades"
    if stage is ValidationStage.WALK_FORWARD:
        return f"{min(float(item['ratio']) for item in figures):.2f} folds"
    if stage is ValidationStage.MONTE_CARLO:
        return f"{min(float(item['probability_of_profit']) for item in figures):.2f} P(profit)"
    if stage is ValidationStage.OUT_OF_SAMPLE:
        return f"{min(float(item['retention']) for item in figures):.2f} retention"
    if stage in (ValidationStage.COSTS, ValidationStage.STRESS):
        factors = [
            -1.0 if item.get("profit_factor") is None else float(item["profit_factor"])
            for item in figures
        ]
        return f"PF {min(factors):.2f}"
    if stage is ValidationStage.PARAMETER_ROBUSTNESS:
        return f"{max(float(item['parameter_dispersion']) for item in figures):.2f} disp."
    return "not measured"


def _count_statuses(report: CampaignReport) -> dict[GateStatus, int]:
    counted = {status: 0 for status in GateStatus}
    for verdict in report.gate_verdicts:
        counted[verdict.status] += 1
    return {status: count for status, count in counted.items() if count}


def gold_market(report: CampaignReport) -> MarketReport | None:
    """The market to promote, found by name: the synthetic gold series or a real XAUUSD."""
    return (
        next((market for market in report.markets if market.market == GOLD), None)
        or next((market for market in report.markets if "XAU" in market.market.upper()), None)
        or next((market for market in report.markets if market.selected is not None), None)
    )


def promotion_evidence(
    report: CampaignReport,
    market: MarketReport,
    candidate: CandidateReport,
    thresholds: AcceptanceThresholds,
) -> PromotionEvidence:
    """The evidence handed to the promotion control, Monte-Carlo probability included.

    The probability field is the whole point: left at `None`, `evaluate_promotion` skips the
    Monte-Carlo check entirely. Building the evidence here, in one named place, is what keeps
    that omission from coming back.
    """
    gates = candidate.gates
    if gates is None:
        raise SystemExit(f"{market.market}:{candidate.label} carries no measurement")
    if candidate.stability_report is None:
        raise SystemExit(f"{market.market}:{candidate.label} carries no stability report")
    correlation = next(
        (
            pair.correlation
            for pair in report.correlations
            if market.market in (pair.market_a, pair.market_b)
        ),
        None,
    )
    return evidence_from_campaign(
        "witness@1.0.0",
        dict(candidate.parameters),
        candidate.train,
        # The out-of-sample performance is the sealed-set reading when there is one, and the
        # validation window otherwise: promoting on a holdout that was never opened would
        # claim a confirmation that did not happen.
        market.out_of_sample if market.out_of_sample is not None else candidate.validation,
        candidate.cost_net,
        candidate.stability_report,
        thresholds,
        correlation_with_existing=correlation,
        monte_carlo_probability_of_profit=gates.probability_of_profit,
    )


def require_monte_carlo_evidence(evidence: PromotionEvidence) -> float:
    """Refuse to decide on evidence that would make the control skip the Monte-Carlo gate.

    `evaluate_promotion` only checks the probability when it is not `None`, so a campaign that
    forgets to pass it silently drops a gate -- the exact defect TASK-066 removes. This is the
    guard that keeps the omission from coming back, and it fails the run rather than the gate.
    """
    probability = evidence.monte_carlo_probability_of_profit
    if probability is None:
        raise SystemExit(
            "the promotion evidence carries no Monte-Carlo probability of profit: the "
            "control would skip the Monte-Carlo gate instead of judging it"
        )
    return probability


def promotion_gate(report: CampaignReport, output: Path) -> None:
    """TASK-065: thresholds are written first, then the decision, then the manifest."""
    market = gold_market(report)
    if market is None or market.selected is None:
        print("== Promotion ==")
        print("  no selected candidate on gold, nothing to promote")
        return
    candidate = market.winner
    if candidate is None:
        print("== Promotion ==")
        print("  the market names a selection it does not carry, nothing to promote")
        return
    thresholds = AcceptanceThresholds(
        version=f"campaign-{SYNTHETIC_SEED}",
        min_trades=30,
        min_stability_score=0.5,
    )
    thresholds_path = write_thresholds(output / "thresholds.json", thresholds)
    evidence = promotion_evidence(report, market, candidate, thresholds)
    probability = require_monte_carlo_evidence(evidence)
    spec = ManifestSpec(
        strategy_id="witness",
        version="1.1.0",
        allowed_symbols=tuple(sorted(datasets_markets(report))),
        timeframes=(TIMEFRAME,),
        history_bars=100,
        parameters=dict(candidate.parameters),
    )
    decided_at = datetime.now(UTC)
    if candidate in report.claims():
        outcome = promote(
            evidence,
            thresholds,
            spec,
            manifest_dir=output / "candidates",
            decision_dir=output / "decisions",
            decided_at=decided_at,
            decided_by="research-campaign",
        )
        reason = ""
    else:
        # The candidate won its market and the correction still demoted it: it is not a
        # discovery. The refusal is recorded like any other decision -- a refusal is a result,
        # and RM-016 wants every decision persisted with its motive -- with the correction as
        # its motive, not a silent crash and not the thresholds taking the blame.
        reason = (
            "no promotion is decided: the candidate won its market but the Benjamini-Hochberg "
            "correction demoted it, so it is not a discovery"
        )
        decision = evaluate_promotion(
            evidence,
            thresholds,
            decided_at=decided_at,
            decided_by="research-campaign",
        )
        decision = replace(
            decision,
            promoted=False,
            reasons=(*decision.reasons, reason),
        )
        decision_path = record_decision(
            output / "decisions" / f"{spec.strategy_id}@{spec.version}.json", decision
        )
        outcome = PromotionOutcome(
            decision=decision, manifest_path=None, decision_path=decision_path
        )
    print("== Promotion ==")
    print(f"  thresholds: {thresholds_path} (digest {thresholds.digest[:12]}…)")
    print(f"  candidate {candidate.label} promoted: {outcome.decision.promoted}")
    print(f"  Monte-Carlo probability of profit checked: {probability:.4f}")
    print(f"  candidate cleared the false-discovery correction: {candidate in report.claims()}")
    print(
        f"  gates cleared by the campaign: "
        f"{', '.join(stage.value for stage in report.passing_stages()) or 'none'}"
    )
    for item in outcome.decision.reasons:
        print(f"    - {item}")
    print(f"  decision: {outcome.decision_path}")
    if outcome.manifest_path is None:
        print("  candidate manifest: none (a refused candidate publishes nothing)")
    else:
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
    parser.add_argument(
        "--monte-carlo-iterations",
        type=int,
        default=MONTE_CARLO_ITERATIONS,
        help="draws of the sign-flip null used to price each candidate",
    )
    args = parser.parse_args()

    if args.datasets is not None:
        datasets = load_markets(args.datasets)
        print(f"loaded {len(datasets)} frozen dataset(s) from {args.datasets}")
    else:
        datasets = synthetic_markets(args.bars)
        print(f"no dataset directory given: generated {len(datasets)} seeded synthetic series")

    thresholds = AcceptanceThresholds(
        version=f"campaign-{SYNTHETIC_SEED}",
        min_trades=30,
        min_stability_score=0.5,
    )
    report = run_campaign(
        datasets,
        candidate_specs(list(datasets)),
        config_for=config_for,
        thresholds=thresholds,
        false_discovery_rate=DEFAULT_FALSE_DISCOVERY_RATE,
        monte_carlo_iterations=args.monte_carlo_iterations,
        confirm_holdout=True,
        anchor=ANCHOR_SPLIT,
        train_fraction=TRAIN_FRACTION,
        validation_fraction=VALIDATION_FRACTION,
    )
    print_report(report, datasets)
    print_windows(report)
    print_gates(report)

    args.output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    json_path = args.output / f"{stamp}-campaign.json"
    json_path.write_text(
        json.dumps(campaign_to_dict(report, datasets, thresholds), indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"report written to {json_path}")
    if datasets:
        promotion_gate(report, args.output)
    print(f"production manifests would be loaded by REGISTRY: {sorted(REGISTRY)}")
    multiple = report.multiple_testing
    if multiple is not None:
        print(
            f"reminder: every candidate the campaign tried was priced — "
            f"{multiple.hypotheses} hypothesis(es) at alpha={multiple.alpha:.2f}, "
            f"Bonferroni threshold {multiple.bonferroni_threshold:.6f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
