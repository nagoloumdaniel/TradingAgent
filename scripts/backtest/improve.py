"""The improvement loop, run for real: propose, measure, compare, keep only what helps.

For each market this script

1. measures the version in production today (its parameters come from
   `config/strategies/*.yaml`, so the baseline is the one actually configured, not a
   convenient one);
2. proposes a bounded set of local variations of those parameters;
3. measures every one of them with the same harness the campaign uses;
4. keeps the first that beats the incumbent by the margin the module requires, or reports
   that nothing did and leaves the incumbent in place.

Why the variations are local, one parameter at a time: a wide grid on six parameters is a
search over thousands of combinations, and the best of thousands beats the incumbent by luck
alone. Coordinate steps keep the number of comparisons small enough to be reported honestly
— `search_improvement` returns that count, and the multiple-testing correction needs it.

Nothing here promotes anything. An accepted variation is a *candidate*: it earns the gates
like every other one, and `max_mode` stays SIGNAL until the protocol says otherwise.

    uv run python scripts/backtest/improve.py
    uv run python scripts/backtest/improve.py --datasets docs/research/datasets
"""

import argparse
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from tradingagent.analytics.model import Performance
from tradingagent.backtest.costs import CostModel
from tradingagent.backtest.datasets import CandleDataset, DatasetStore
from tradingagent.backtest.harness import BacktestConfig
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.campaign import CandidateSpec, MarketReport, run_campaign
from tradingagent.research.improvement import (
    Candidate,
    ImprovementOutcome,
    Measurement,
    report,
    search_improvement,
)
from tradingagent.strategies.library.trend_breakout import TrendBreakout, TrendBreakoutParameters
from tradingagent.strategies.library.witness import Witness, WitnessParameters
from tradingagent.strategies.manifest import StrategyManifest

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "docs" / "research" / "candidates"
STRATEGY_CONFIG_DIR = ROOT / "config" / "strategies"
TIMEFRAME = Timeframe.M15

# The version each market runs today. Read from the production manifests so the baseline
# cannot silently drift from what the agent actually loads.
INCUMBENTS = {
    "XAUUSD": "witness@1.1.0",
    "BTCUSD": "trend_breakout@1.0.0",
}

# One step per parameter per direction: a local search, small enough to report honestly.
STEP_RELATIVE = 0.25
MAX_ATTEMPTS_PER_MARKET = 12


def read_manifest(path: Path) -> Mapping[str, Any]:
    """The production manifest, as written. A tiny YAML subset is not worth a dependency."""
    import yaml

    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def build_factory(strategy_id: str, parameters: Mapping[str, float]):
    if strategy_id == "witness":
        return lambda values: Witness(WitnessParameters(**values))
    if strategy_id == "trend_breakout":
        return lambda values: TrendBreakout(TrendBreakoutParameters(**values))
    raise SystemExit(f"no factory for strategy {strategy_id}")


def manifest_for(document: Mapping[str, Any], market: str) -> StrategyManifest:
    return StrategyManifest.model_validate(
        {
            "strategy_id": document["strategy_id"],
            "version": document["version"],
            "max_mode": document["max_mode"],
            "allowed_symbols": [market],
            "timeframes": [TIMEFRAME.value],
            "history_bars": document["history_bars"],
            "expiry_bars": document["expiry_bars"],
        }
    )


def incumbent_spec(market: str) -> CandidateSpec:
    ref = INCUMBENTS[market]
    document = read_manifest(STRATEGY_CONFIG_DIR / f"{ref}.yaml")
    # The YAML's own types are kept: a period is an `int`, and turning it into a float here
    # makes the campaign's own perturbation produce 15.4 periods — which the parameter model
    # rightly refuses. `perturb_parameters` already knows how to nudge an integer.
    parameters = {
        key: value
        for key, value in document["parameters"].items()
        if isinstance(value, int | float)
    }
    return CandidateSpec(
        label=f"actuelle-{ref}",
        manifest=manifest_for(document, market),
        factory=build_factory(document["strategy_id"], parameters),
        parameters=parameters,
    )


def stepped(value: float, relative: float) -> float:
    """One step up or down, kept in the shape the parameter actually has.

    An integer parameter stays integral: a period of 17.5 is not a strategy, and the model
    that validates it is right to refuse it.
    """
    target = value * (1.0 + relative)
    if isinstance(value, int) or float(value).is_integer():
        return int(max(2, round(target)))
    return round(target, 4)


def variations(incumbent: CandidateSpec) -> list[CandidateSpec]:
    """One parameter moved at a time, up and down, inside the search budget."""
    found: list[CandidateSpec] = []
    for name, value in incumbent.parameters.items():
        for direction in (+STEP_RELATIVE, -STEP_RELATIVE):
            moved = dict(incumbent.parameters)
            moved[name] = stepped(value, direction)
            if moved[name] == value:
                continue
            if any(existing.parameters == moved for existing in found):
                continue
            arrow = "+" if direction > 0 else "-"
            found.append(
                CandidateSpec(
                    label=f"{name}{arrow}{int(STEP_RELATIVE * 100)}%",
                    manifest=incumbent.manifest,
                    factory=incumbent.factory,
                    parameters=moved,
                )
            )
    return found[:MAX_ATTEMPTS_PER_MARKET]


def config_for(market: str, dataset: CandleDataset) -> BacktestConfig:
    price = dataset.candles[0].close
    return BacktestConfig(
        symbol=market,
        costs=CostModel(
            spread=round(price * 0.00005, 6),
            slippage_fixed=round(price * 0.00002, 6),
            commission_per_trade=Decimal("0.5"),
        ),
        mode=TradingMode.SIGNAL,
        max_concurrent_positions=1,
    )


def objective_of(performance: Performance) -> float:
    """Net profit after costs and stress: the figure the operator is paid in.

    Stability and profit factor are guards, not the objective. Maximising a robustness score
    would reward the shape of a measurement rather than the result it stands for.
    """
    return float(performance.net_profit)


def risk_guard(incumbent: Measurement, measured: Measurement) -> str | None:
    """An improvement that draws down further is not an improvement."""
    allowed = float(incumbent.metrics.get("max_drawdown", 0.0)) * 1.10
    drawn = float(measured.metrics.get("max_drawdown", 0.0))
    if allowed > 0 and drawn > allowed:
        return f"drawdown {drawn:.2f} above the {allowed:.2f} allowed (the incumbent's +10 %)"
    floor = float(incumbent.metrics.get("profit_factor", 0.0))
    if floor > 0 and float(measured.metrics.get("profit_factor", 0.0)) < floor:
        return (
            f"profit factor {measured.metrics.get('profit_factor', 0.0):.2f} "
            f"below the incumbent's {floor:.2f}"
        )
    return None


def measurement_of(candidate: Any) -> Measurement:
    return Measurement(
        objective=objective_of(candidate.cost_net),
        metrics={
            "max_drawdown": float(candidate.cost_net.max_drawdown),
            "profit_factor": float(candidate.cost_net.profit_factor),
            "trades": float(candidate.cost_net.trades),
            "stability": float(candidate.stability_score),
        },
    )


def improve_market(
    market: str, dataset: CandleDataset
) -> tuple[ImprovementOutcome, MarketReport, dict[str, float]]:
    incumbent = incumbent_spec(market)
    proposals = variations(incumbent)
    if not proposals:
        raise SystemExit(f"{market}: no variation to try")

    campaign = run_campaign(
        {market: dataset},
        [incumbent, *proposals],
        config_for=config_for,
    )
    market_report = campaign.markets[0]
    by_label = {item.label: item for item in market_report.candidates}
    baseline = measurement_of(by_label[incumbent.label])
    reasons = " ; ".join(by_label[incumbent.label].reasons) or "aucun motif d'échec mesuré"

    outcome = search_improvement(
        market=market,
        incumbent=baseline,
        incumbent_label=INCUMBENTS[market],
        candidates=[
            Candidate(
                label=proposal.label,
                parameters=proposal.parameters,
                rationale=f"variation locale de {proposal.label} (échec mesuré : {reasons})",
                payload=proposal,
            )
            for proposal in proposals
        ],
        evaluate=lambda candidate: measurement_of(by_label[candidate.label]),
        guard=risk_guard,
        max_attempts=MAX_ATTEMPTS_PER_MARKET,
    )
    return outcome, market_report, dict(incumbent.parameters)


def write_candidate(outcome: ImprovementOutcome, market: str, output: Path) -> Path | None:
    """A candidate manifest, never a production one: promotion is the protocol's job."""
    accepted = next((attempt for attempt in outcome.attempts if attempt.accepted), None)
    if accepted is None:
        return None
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"{market}-{outcome.incumbent_label}.json"
    payload = {
        "market": market,
        "supersedes": outcome.incumbent_label,
        "proposed_label": accepted.label,
        "parameters": {key: float(value) for key, value in accepted.parameters.items()},
        "incumbent_objective": outcome.incumbent_objective,
        "proposed_objective": accepted.objective,
        "relative_gain": outcome.relative_gain,
        "comparisons_made": outcome.trials,
        "decided_at": datetime.now(UTC).isoformat(),
        "note": (
            "Candidat, pas une promotion. max_mode reste SIGNAL tant que les portes de "
            f"validation ne sont pas franchies. {outcome.trials} comparaison(s) ont été "
            "faites : ce nombre doit entrer dans la correction du test multiple."
        ),
        "attempts": [
            {
                "number": attempt.number,
                "label": attempt.label,
                "accepted": attempt.accepted,
                "objective": attempt.objective,
                "reason": attempt.reason,
                "parameters": {key: float(value) for key, value in attempt.parameters.items()},
            }
            for attempt in outcome.attempts
        ],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", type=Path, default=ROOT / "docs" / "research" / "datasets")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    datasets: dict[str, CandleDataset] = DatasetStore(args.datasets).load_all()
    if not datasets:
        raise SystemExit(f"no dataset found in {args.datasets}")

    written: list[Path] = []
    improved = 0
    for market in sorted(datasets):
        if market not in INCUMBENTS:
            print(f"== {market} : aucun enregistrement de production, ignoré ==")
            continue
        outcome, _, _ = improve_market(market, datasets[market])
        print(report(outcome))
        print()
        path = write_candidate(outcome, market, args.output)
        if path is not None:
            written.append(path)
            improved += 1

    print("== Bilan ==")
    print(f"  marchés améliorés : {improved} / {len([m for m in datasets if m in INCUMBENTS])}")
    for path in written:
        print(f"  candidat écrit : {path}")
    if not written:
        print("  Aucun candidat : les versions en production restent les meilleures mesurées.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__: Sequence[str] = [
    "INCUMBENTS",
    "incumbent_spec",
    "main",
    "measurement_of",
    "risk_guard",
    "variations",
]
