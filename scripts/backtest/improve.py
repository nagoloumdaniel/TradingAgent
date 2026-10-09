"""The improvement loop, run for real: propose, measure, compare, keep only what helps.

For each market this script

1. measures the version in production today (its parameters come from
   `config/strategies/*.yaml`, so the baseline is the one actually configured, not a
   convenient one);
2. proposes a bounded set of local variations of those parameters;
3. measures every one of them with the same harness the campaign uses;
4. keeps the first that beats the incumbent by the margin the module requires, or reports
   that nothing did and leaves the incumbent in place;
5. hands the whole comparison to `ai.improvement_cycle`, which produces the candidate version
   when one won, and writes the measurement into `backtest_runs`.

Why the variations are local, one parameter at a time: a wide grid on six parameters is a
search over thousands of combinations, and the best of thousands beats the incumbent by luck
alone. Coordinate steps keep the number of comparisons small enough to be reported honestly
— `search_improvement` returns that count, and the multiple-testing correction needs it.

**What step 5 changes, and why it matters.** Until the chain existed, this script measured
everything and printed it: `backtest_runs` stayed empty, so the AI researcher read no proof and
proposed nothing, ever. Now the measured baseline and the accepted candidate are recorded as
evidence, and `DailyLab._evidence` finds them.

Nothing here promotes anything. An accepted variation is a *candidate*: it earns the gates
like every other one, `max_mode` stays SIGNAL, and `write_candidate` still refuses to write
under `config/strategies/`.

    uv run python scripts/backtest/improve.py
    uv run python scripts/backtest/improve.py --datasets docs/research/datasets
    uv run python scripts/backtest/improve.py --no-record   # measure without touching the DB
"""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tradingagent.ai.escalation import DEFAULT_TRIGGER, Escalation, FailurePattern
from tradingagent.ai.evidence import MarketEvidence, MeasuredRun, performance_metrics, record_run
from tradingagent.ai.improvement_cycle import (
    CycleOutcome,
    CycleStatus,
    ImprovementCycle,
    Measurement,
    Variant,
)
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
from tradingagent.research.improvement import (
    Candidate,
    ImprovementOutcome,
    report,
    search_improvement,
)
from tradingagent.research.versioning import CandidateVersion, build_candidate, write_candidate
from tradingagent.strategies.library.trend_breakout import TrendBreakout, TrendBreakoutParameters
from tradingagent.strategies.library.witness import Witness, WitnessParameters
from tradingagent.strategies.manifest import StrategyManifest

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "docs" / "research" / "candidates"
STRATEGY_CONFIG_DIR = ROOT / "config" / "strategies"
TIMEFRAME = Timeframe.M15

# The version each market runs today is *read*, not declared here: `config/agent.yaml` is the
# file the agent itself loads at start-up, and `deployed_refs` below is the only place that
# reads it. This comment used to claim the refs came from the production manifests while the
# code hard-coded them; on 2026-10-08 the script still compared against `witness@1.1.0` while
# the agent ran `witness@1.1.1`, so every recorded run named a version nobody was running.
AGENT_CONFIG = ROOT / "config" / "agent.yaml"

# One step per parameter per direction: a local search, small enough to report honestly.
STEP_RELATIVE = 0.25
MAX_ATTEMPTS_PER_MARKET = 12
# What the escalation of step 5 is called. It is not a loss motif: it is the measured
# fragility of the version in place, which is what sent this script here.
FRAGILITY_KIND = "fragilite_backtest"


def read_manifest(path: Path) -> Mapping[str, Any]:
    """The production manifest, as written. A tiny YAML subset is not worth a dependency."""
    import yaml

    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def deployed_refs(path: Path = AGENT_CONFIG) -> dict[str, str]:
    """What the agent loads for each market, as `config/agent.yaml` declares it.

    A disabled market is left out on purpose: measuring a strategy the agent is not watching
    would produce evidence for a version that earns nothing, and the daily chain compares
    against whatever this file names.
    """
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


def incumbent_ref(market: str, path: Path = AGENT_CONFIG) -> str:
    """The version in place for this market, or a refusal that names what is declared.

    Refusing beats falling back to a remembered list: a baseline measured against a version
    the agent does not run is worse than no measurement, because it looks like one.
    """
    refs = deployed_refs(path)
    if market not in refs:
        declared = ", ".join(sorted(refs)) or "aucun marche"
        raise SystemExit(
            f"{market}: {path.name} ne declare aucune strategie pour ce marche "
            f"(declares : {declared})"
        )
    return refs[market]


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


def incumbent_spec(market: str, ref: str | None = None) -> CandidateSpec:
    """The version in place, as a campaign candidate.

    `ref` is injectable so a test can pin the baseline it measures against; in production it is
    left out and read from `config/agent.yaml` (`deployed_refs`), which is the only source of
    truth about what the agent runs.
    """
    ref = ref or incumbent_ref(market)
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
        costs=campaign_costs(price),
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
    """An improvement that draws down further is not an improvement.

    It reads the same metric names the AI Lab reads (`research_metrics`), so what the search
    guards on and what the researcher later reasons about cannot drift apart.
    """
    allowed = float(incumbent.metrics.get("max_drawdown_eur", 0.0)) * 1.10
    drawn = float(measured.metrics.get("max_drawdown_eur", 0.0))
    if allowed > 0 and drawn > allowed:
        return f"drawdown {drawn:.2f} above the {allowed:.2f} allowed (the incumbent's +10 %)"
    floor = float(incumbent.metrics.get("profit_factor_net", 0.0))
    if floor > 0 and float(measured.metrics.get("profit_factor_net", 0.0)) < floor:
        return (
            f"profit factor {measured.metrics.get('profit_factor_net', 0.0):.2f} "
            f"below the incumbent's {floor:.2f}"
        )
    return None


def measurement_of(candidate: Any) -> Measurement:
    """What the search compares on: the measured figures, under the names the lab reads.

    An undefined ratio is left out rather than written as zero, and the metrics are the same
    ones `record_run` stores — so the number the search accepted is the number the researcher
    finds, with no translation in between.
    """
    return Measurement(
        objective=objective_of(candidate.cost_net), metrics=research_metrics(candidate)
    )


def research_metrics(candidate: CandidateReport) -> dict[str, float]:
    """The figures the AI Lab reads, under the names it reads them by.

    `ai.researcher` looks for `max_drawdown_eur`, `parameter_dispersion` and
    `most_sensitive_parameter`; only what the campaign actually measured is written, so a
    missing key means "not measured" rather than "zero".
    """
    metrics = performance_metrics(candidate.cost_net)
    stability = candidate.stability_report
    if stability is not None:
        metrics["stability_score"] = float(stability.score)
        metrics["parameter_dispersion"] = float(stability.parameter_dispersion)
        metrics["out_of_sample_retention"] = float(stability.out_of_sample_retention)
        metrics["profitable_regime_ratio"] = float(stability.profitable_regime_ratio)
    return metrics


def costs_payload(market: str, dataset: CandleDataset) -> dict[str, Any]:
    """The cost model the run was measured under, as JSON. A run without it is not reproducible."""
    costs = config_for(market, dataset).costs
    return {
        "spread": float(costs.spread),
        "slippage_atr_fraction": float(costs.slippage_atr_fraction),
        "slippage_fixed": float(costs.slippage_fixed),
        "commission_per_trade": float(costs.commission_per_trade),
        "execution_delay_bars": int(costs.execution_delay_bars),
        "multiplier": float(costs.multiplier),
    }


@dataclass(frozen=True)
class MeasuredMarket:
    """One campaign over one market: the candidates, their reports and the dataset used."""

    market: str
    dataset: CandleDataset
    incumbent: CandidateSpec
    proposals: tuple[CandidateSpec, ...]
    report: MarketReport

    def by_label(self) -> dict[str, CandidateReport]:
        return {candidate.label: candidate for candidate in self.report.candidates}

    def baseline_report(self) -> CandidateReport:
        return self.by_label()[self.incumbent.label]

    def baseline(self) -> Measurement:
        return measurement_of(self.baseline_report())


def measure_market(market: str, dataset: CandleDataset) -> MeasuredMarket:
    """The campaign step: the incumbent and every variation, measured on identical data."""
    incumbent = incumbent_spec(market)
    proposals = variations(incumbent)
    if not proposals:
        raise SystemExit(f"{market}: no variation to try")
    campaign = run_campaign(
        {market: dataset},
        [incumbent, *proposals],
        config_for=config_for,
    )
    return MeasuredMarket(
        market=market,
        dataset=dataset,
        incumbent=incumbent,
        proposals=tuple(proposals),
        report=campaign.markets[0],
    )


def escalation_for(measured: MeasuredMarket) -> Escalation:
    """The escalation that sends this market into the chain.

    This script has no loss history to escalate from: what sent it here is the fragility the
    campaign measured on the version in place, and saying so is more honest than inventing a
    failure motif. The `kind` is therefore not a `LossKind`, and nothing routes on it.
    """
    baseline = measured.baseline_report()
    reasons = tuple(baseline.reasons) or ("aucun motif d'échec mesuré",)
    pattern = FailurePattern(
        market=measured.market,
        kind=FRAGILITY_KIND,
        occurrences=max(1, len(baseline.reasons)),
        first_seen=measured.dataset.candles[0].open_time,
        last_seen=measured.dataset.candles[-1].open_time,
        reasons=reasons[:3],
    )
    return Escalation(
        market=measured.market,
        pattern=pattern,
        trigger=DEFAULT_TRIGGER,
        reason=f"la campagne mesure {len(baseline.reasons)} faiblesse(s) sur la version en place",
    )


def evidence_context(measured: MeasuredMarket, ref: str | None = None) -> MarketEvidence:
    """What the chain must know to record a candidate against the same dataset.

    Handed in rather than read back, because at this point nothing has been recorded yet: the
    baseline of this very campaign *is* the proof, and it is recorded as such below. `ref` names
    the version this measurement belongs to; left out, it is read from `config/agent.yaml`.
    """
    baseline = measured.baseline_report()
    candles = measured.dataset.candles
    return MarketEvidence(
        market=measured.market,
        ref=ref or incumbent_ref(measured.market),
        dataset_id=measured.dataset.dataset_id,
        fingerprint=measured.dataset.fingerprint,
        window_start=candles[0].open_time,
        window_end=candles[-1].open_time,
        parameters={key: float(value) for key, value in measured.incumbent.parameters.items()},
        metrics=research_metrics(baseline),
        costs=costs_payload(measured.market, measured.dataset),
        objective=objective_of(baseline.cost_net),
        comparisons=None,
        validations=(),
    )


def measured_run(
    measured: MeasuredMarket,
    *,
    ref: str,
    candidate: CandidateReport,
    comparisons: int | None,
) -> MeasuredRun:
    """A campaign report, as the run the bridge stores."""
    candles = measured.dataset.candles
    return MeasuredRun(
        market=measured.market,
        ref=ref,
        dataset_id=measured.dataset.dataset_id,
        fingerprint=measured.dataset.fingerprint,
        window_start=candles[0].open_time,
        window_end=candles[-1].open_time,
        objective=objective_of(candidate.cost_net),
        metrics=research_metrics(candidate),
        costs=costs_payload(measured.market, measured.dataset),
        comparisons=comparisons,
    )


def cycle_for(
    market: str,
    *,
    measured: MeasuredMarket,
    output: Path,
    engine: Any,
    max_attempts: int = MAX_ATTEMPTS_PER_MARKET,
    ref: str | None = None,
) -> ImprovementCycle:
    """The production wiring: the real search, the real version builder, the real refusal.

    This is the only place where the `research` machinery meets the chain, and it does so
    outside `src/`: the architecture forbids a production package from importing research
    (`tests/test_architecture.py`). The measurement is a lookup into the campaign that already
    ran — every variation was measured once, on identical data, which is the whole point.

    `ref` is the version the search compares against; left out, it is the one the agent loads.
    """
    by_label = measured.by_label()
    ref = ref or incumbent_ref(market)
    baseline = measured.baseline()
    variants = tuple(
        Variant(
            label=spec.label,
            parameters=dict(spec.parameters),
            rationale=(
                "variation locale de "
                f"{spec.label} (échec mesuré : {' ; '.join(measured.baseline_report().reasons)})"
            ),
            payload=spec,
        )
        for spec in measured.proposals
    )
    by_variant = {variant.label: variant for variant in variants}

    def measure(variant: Variant, evidence: MarketEvidence) -> Measurement:
        return measurement_of(by_label[variant.label])

    def search(candidates: Sequence[Variant], evaluate: Any) -> ImprovementOutcome:
        def evaluate_candidate(candidate: Candidate) -> Measurement:
            return evaluate(by_variant[candidate.label])

        return search_improvement(
            market=market,
            incumbent=baseline,
            incumbent_label=ref,
            candidates=[
                Candidate(
                    label=variant.label,
                    parameters=dict(variant.parameters),
                    rationale=variant.rationale,
                    payload=variant.payload,
                )
                for variant in candidates
            ],
            evaluate=evaluate_candidate,
            guard=risk_guard,
            max_attempts=max_attempts,
        )

    def propose(evidence: MarketEvidence, escalation: Escalation) -> Sequence[Variant]:
        return variants

    def build(
        market_name: str, supersedes: str, parameters: Mapping[str, float]
    ) -> CandidateVersion:
        document = read_manifest(STRATEGY_CONFIG_DIR / f"{supersedes}.yaml")
        return build_candidate(
            market=market_name,
            supersedes=supersedes,
            parameters=parameters,
            incumbent_manifest=document,
        )

    def write(candidate: CandidateVersion) -> Path:
        return write_candidate(candidate, output)

    return ImprovementCycle(
        engine,
        propose=propose,
        measure=measure,
        search=search,
        build=build,
        write=write,
    )


def record_baseline(
    engine: Any,
    measured: MeasuredMarket,
    *,
    comparisons: int | None,
    at: datetime,
    ref: str | None = None,
) -> int:
    """Record the version in place, with the number of comparisons that left it in place.

    This is the closing statement of a search that improved nothing: the next daily pass reads
    it as evidence, and the count travels with it.
    """
    return record_run(
        engine,
        measured_run(
            measured,
            ref=ref or incumbent_ref(measured.market),
            candidate=measured.baseline_report(),
            comparisons=comparisons,
        ),
        at=at,
    )


@dataclass(frozen=True)
class MarketOutcome:
    """What one market produced: the campaign, and the chain's verdict when it ran."""

    market: str
    measured: MeasuredMarket
    cycle: CycleOutcome


def run_market(
    market: str,
    dataset: CandleDataset,
    output: Path,
    *,
    engine: Any | None = None,
    at: datetime | None = None,
) -> MarketOutcome:
    """Measure one market, then let the chain produce the candidate and the evidence.

    Without an engine the chain still runs the whole comparison — its journal and its evidence
    recording are simply skipped — so `--no-record` measures exactly as it always did.
    """
    moment = at or datetime.now(UTC)
    measured = measure_market(market, dataset)
    outcome = cycle_for(market, measured=measured, output=output, engine=engine).run(
        escalation_for(measured),
        at=moment,
        incumbent=measured.baseline(),
        evidence=evidence_context(measured),
    )
    if engine is not None and outcome.status is CycleStatus.NO_IMPROVEMENT:
        record_baseline(engine, measured, comparisons=outcome.comparisons or None, at=moment)
    return MarketOutcome(market=market, measured=measured, cycle=outcome)


def write_attempts(
    outcome: ImprovementOutcome | CycleOutcome, market: str, output: Path, ref: str | None = None
) -> Path:
    """The audit trail of one search, on disk: every attempt, kept or refused.

    `backtest_runs` holds the measurements that describe a version; this holds the *search* —
    which variants were tried, why each was turned down, and how many comparisons were made.
    That count is the input of the multiple-testing correction, and losing it is how a lucky
    variant gets mistaken for a discovery.
    """
    output.mkdir(parents=True, exist_ok=True)
    label = outcome.ref or ref or incumbent_ref(market)
    path = output / f"{market}-{label}.json"
    search = outcome if isinstance(outcome, ImprovementOutcome) else outcome.search
    comparisons = outcome.comparisons if search is None else search.trials
    accepted = next((attempt for attempt in outcome.attempts if attempt.accepted), None)
    payload = {
        "market": market,
        "supersedes": label,
        "status": outcome.status.value if isinstance(outcome, CycleOutcome) else None,
        "proposed_label": outcome.accepted_label,
        "parameters": (
            {key: float(value) for key, value in dict(accepted.parameters).items()}
            if accepted is not None
            else None
        ),
        "incumbent_objective": outcome.incumbent_objective,
        "proposed_objective": None if accepted is None else accepted.objective,
        "relative_gain": outcome.relative_gain,
        "comparisons_made": comparisons,
        "decided_at": datetime.now(UTC).isoformat(),
        "note": (
            "Candidat, pas une promotion. max_mode reste SIGNAL tant que les portes de "
            f"validation ne sont pas franchies. {comparisons} comparaison(s) ont été faites : "
            "ce nombre doit entrer dans la correction du test multiple."
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


def engine_from_environment() -> Any:
    """The production database, for recording evidence. Never a research-only database."""
    from tradingagent.config.settings import load_database_settings
    from tradingagent.storage.engine import create_database_engine

    settings = load_database_settings(ROOT / ".env")
    return create_database_engine(settings.database_url.get_secret_value())


def _use_utf8_when_redirected() -> None:
    """A redirected Windows pipe defaults to a legacy code page, which cannot encode the
    emoji the chain's message carries. Ask for UTF-8 instead of crashing once it is logged."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and not stream.isatty():
            reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    _use_utf8_when_redirected()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", type=Path, default=ROOT / "docs" / "research" / "datasets")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--no-record",
        action="store_true",
        help="measure without recording evidence: nothing reaches backtest_runs",
    )
    parser.add_argument(
        "--at",
        default=None,
        help="instant UTC de référence au format ISO 8601 ; par défaut, maintenant",
    )
    args = parser.parse_args()

    datasets: dict[str, CandleDataset] = DatasetStore(args.datasets).load_all()
    if not datasets:
        raise SystemExit(f"no dataset found in {args.datasets}")

    engine = None
    if not args.no_record:
        try:
            engine = engine_from_environment()
        except Exception as error:  # a database that is down must not lose the measurement
            print(
                "== Aucune base de données joignable : les mesures ne seront PAS enregistrées "
                f"dans backtest_runs ({type(error).__name__}: {error}) =="
            )

    moment = datetime.fromisoformat(args.at) if args.at else datetime.now(UTC)

    written: list[Path] = []
    improved = 0
    # What the agent loads, read once: `config/agent.yaml`. A market it does not declare is
    # not measured, and the reason is printed instead of guessed.
    refs = deployed_refs()
    eligible = [market for market in sorted(datasets) if market in refs]
    for market in sorted(datasets):
        if market not in refs:
            print(f"== {market} : absent de {AGENT_CONFIG.name} ==")
            print("   aucun enregistrement de production pour ce marche, il n'est pas mesure")
            continue
        result = run_market(market, datasets[market], args.output, engine=engine, at=moment)
        search = result.cycle.search
        if search is not None:
            print(report(search))
        print(result.cycle.message())
        print()
        if result.cycle.status is CycleStatus.IMPROVED:
            improved += 1
        written.append(write_attempts(result.cycle, market, args.output))

    print("== Bilan ==")
    print(f"  marchés améliorés : {improved} / {len(eligible)}")
    for path in written:
        print(f"  journal de recherche : {path}")
    if improved == 0:
        print("  Aucun candidat : les versions en production restent les meilleures mesurées.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__: Sequence[str] = [
    "MarketOutcome",
    "MeasuredMarket",
    "costs_payload",
    "cycle_for",
    "deployed_refs",
    "escalation_for",
    "evidence_context",
    "incumbent_ref",
    "incumbent_spec",
    "main",
    "measure_market",
    "measured_run",
    "measurement_of",
    "record_baseline",
    "research_metrics",
    "risk_guard",
    "run_market",
    "variations",
    "write_attempts",
]
