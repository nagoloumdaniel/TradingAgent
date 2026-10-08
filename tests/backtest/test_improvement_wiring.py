"""The chain, wired for real: `search_improvement`, `build_candidate`, `write_candidate`.

`tests/ai` proves the sequence with an injected search. This file proves the *production*
wiring — the one `scripts/backtest/improve.py` uses — behaves when the real acceptance rule,
the real version builder and the real refusal of `config/strategies/` are plugged in:

* a variant that genuinely beats the incumbent by more than the required margin (and does not
  draw down further) produces a candidate manifest under the research directory;
* the accepted measurement lands in `backtest_runs` with its comparison count, so the next
  daily pass reads the candidate as evidence;
* a search that improves nothing records the version in place instead, and writes no version;
* a measurement that raises is refused, counted and journalled — never swallowed;
* `write_candidate`'s refusal is not bypassed: the chain surfaces it.

The measurement is injected, so none of this needs a market: the campaign is a lookup table of
already-measured candidates, exactly as it is in the script after `run_campaign` returns.
"""

import importlib
from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tradingagent.ai.evidence import evidence_for
from tradingagent.ai.improvement_cycle import (
    CYCLE_EVENT,
    MEASURE_FAILED_EVENT,
    CycleStatus,
    ImprovementCycle,
    Measurement,
    Variant,
)
from tradingagent.analytics.model import Performance
from tradingagent.analytics.performance import compute_performance
from tradingagent.backtest.datasets import CandleDataset, SyntheticRegime, synthetic_dataset
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.campaign import CandidateReport, CandidateSpec, MarketReport
from tradingagent.research.improvement import Candidate, search_improvement
from tradingagent.research.improvement import Measurement as SearchMeasurement
from tradingagent.research.protocol import StabilityReport
from tradingagent.research.versioning import build_candidate, write_candidate
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import BacktestRunRow, SystemEventRow
from tradingagent.strategies.library.witness import Witness, WitnessParameters
from tradingagent.strategies.manifest import StrategyManifest

improve = importlib.import_module("scripts.backtest.improve")

T0 = datetime(2026, 10, 8, 6, 0, tzinfo=UTC)
MARKET = "XAUUSD"
REF = "witness@1.1.0"
PARAMETERS = {
    "ema_fast": 20,
    "ema_slow": 50,
    "atr_period": 14,
    "stop_atr_multiplier": 1.5,
    "take_profit_rr": 2.0,
    "entry_zone_atr": 0.1,
}


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'wiring.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


def a_dataset(bars: int = 60) -> CandleDataset:
    return synthetic_dataset(
        "wiring-test",
        MARKET,
        Timeframe.M15,
        datetime(2026, 1, 5, tzinfo=UTC),
        (SyntheticRegime(bars=bars, drift=0.00002, volatility=0.0012),),
        seed=7,
        start_price=2000.0,
        decimals=2,
    )


def a_performance(*, net: float, drawdown: float, trades: int, factor: float | None) -> Performance:
    """A measured result, shaped like the ones `run_campaign` returns."""
    return replace(
        compute_performance([]),
        net_profit=Decimal(str(net)),
        max_drawdown=Decimal(str(drawdown)),
        trades=trades,
        profit_factor=factor,
    )


def a_stability(score: float, dispersion: float, reasons: tuple[str, ...] = ()) -> StabilityReport:
    return StabilityReport(
        score=score,
        out_of_sample_retention=0.8,
        parameter_dispersion=dispersion,
        profitable_regime_ratio=0.6,
        trades=120,
        fragile=bool(reasons),
        reasons=reasons,
    )


def a_candidate(
    label: str,
    *,
    net: float,
    drawdown: float = 100.0,
    factor: float | None = 1.6,
    score: float = 0.7,
    parameters: Mapping[str, float] | None = None,
    reasons: tuple[str, ...] = (),
) -> CandidateReport:
    performance = a_performance(net=net, drawdown=drawdown, trades=120, factor=factor)
    moved = {**PARAMETERS, **(parameters or {})}
    return CandidateReport(
        market=MARKET,
        label=label,
        train=performance,
        validation=performance,
        cost_net=performance,
        stability_score=score,
        fragile=bool(reasons),
        reasons=reasons,
        selected=False,
        stability_report=a_stability(score, 0.1, reasons),
        parameters=moved,
    )


def a_measured_market(reports: list[CandidateReport]) -> Any:
    """What `measure_market` returns, with the campaign replaced by measured reports."""
    incumbent = CandidateSpec(
        label=f"actuelle-{REF}",
        manifest=StrategyManifest.model_validate(
            {
                "strategy_id": "witness",
                "version": "1.1.0",
                "max_mode": "SIGNAL",
                "allowed_symbols": [MARKET],
                "timeframes": ["M15"],
                "history_bars": 300,
                "expiry_bars": 1,
            }
        ),
        factory=lambda values: Witness(WitnessParameters(**values)),
        parameters=dict(PARAMETERS),
    )
    proposals = tuple(
        CandidateSpec(
            label=report.label,
            manifest=incumbent.manifest,
            factory=incumbent.factory,
            parameters=dict(report.parameters),
        )
        for report in reports
        if report.label != incumbent.label
    )
    return improve.MeasuredMarket(
        market=MARKET,
        dataset=a_dataset(),
        incumbent=incumbent,
        proposals=proposals,
        report=MarketReport(
            market=MARKET,
            dataset_id="wiring-test",
            timeframe=Timeframe.M15,
            candidates=tuple(reports),
            selected=reports[0].label,
            holdout_still_sealed=True,
        ),
    )


def a_fragile_market() -> Any:
    """Incumbent at 1000, a timid variation at 1010 (+1 %), a strong one at 1400 (+40 %)."""
    return a_measured_market(
        [
            a_candidate(
                f"actuelle-{REF}",
                net=1000.0,
                score=0.4,
                reasons=("parameter dispersion 0.62 above 0.50",),
            ),
            a_candidate("variation-timide", net=1010.0),
            a_candidate("variation-forte", net=1400.0, drawdown=105.0),
        ]
    )


def a_stubborn_market() -> Any:
    """Nothing beats the incumbent by the margin the search requires."""
    return a_measured_market(
        [
            a_candidate(f"actuelle-{REF}", net=1000.0, score=0.4),
            a_candidate("variation-timide", net=1010.0),
            a_candidate("variation-faible", net=1050.0),
        ]
    )


def rows(engine: Engine) -> list[BacktestRunRow]:
    with Session(engine) as session:
        return list(session.scalars(select(BacktestRunRow).order_by(BacktestRunRow.id)).all())


def journal(engine: Engine, kind: str) -> list[SystemEventRow]:
    with Session(engine) as session:
        return list(
            session.scalars(
                select(SystemEventRow)
                .where(SystemEventRow.kind == kind)
                .order_by(SystemEventRow.id)
            ).all()
        )


# --- the real chain, on the real acceptance rule ------------------------------------------------


def test_a_real_improvement_produces_a_candidate_version(engine: Engine, tmp_path: Path) -> None:
    measured = a_fragile_market()

    outcome = improve.cycle_for(
        MARKET, measured=measured, output=tmp_path, engine=engine, ref=REF
    ).run(
        improve.escalation_for(measured),
        at=T0,
        incumbent=measured.baseline(),
        evidence=improve.evidence_context(measured, ref=REF),
    )

    assert outcome.status is CycleStatus.IMPROVED
    assert outcome.accepted_label == "variation-forte", "the timid +1 % must not be kept"
    assert outcome.comparisons == 2
    assert outcome.candidate_ref == "witness@1.1.1"
    manifest = tmp_path / "witness-1.1.1.yaml"
    assert outcome.candidate_path == manifest
    assert manifest.exists()
    assert manifest.parent == tmp_path, "the candidate lands under the research directory"
    assert "take_profit_rr: 2.0" in manifest.read_text(encoding="utf-8")


def test_the_accepted_measurement_becomes_the_evidence_the_lab_reads(
    engine: Engine, tmp_path: Path
) -> None:
    measured = a_fragile_market()

    improve.cycle_for(MARKET, measured=measured, output=tmp_path, engine=engine, ref=REF).run(
        improve.escalation_for(measured),
        at=T0,
        incumbent=measured.baseline(),
        evidence=improve.evidence_context(measured, ref=REF),
    )

    evidence = evidence_for(engine, MARKET)
    assert evidence is not None
    assert evidence.ref == "witness@1.1.1"
    assert evidence.comparisons == 2
    assert evidence.objective == 1400.0
    assert evidence.metrics["max_drawdown_eur"] == 105.0
    assert evidence.dataset_id == "wiring-test"


def test_the_comparison_count_reaches_the_journal_too(engine: Engine, tmp_path: Path) -> None:
    measured = a_fragile_market()

    improve.cycle_for(MARKET, measured=measured, output=tmp_path, engine=engine, ref=REF).run(
        improve.escalation_for(measured),
        at=T0,
        incumbent=measured.baseline(),
        evidence=improve.evidence_context(measured, ref=REF),
    )

    entries = journal(engine, CYCLE_EVENT)
    assert len(entries) == 1
    assert entries[0].detail["comparisons"] == 2
    assert entries[0].detail["accepted_label"] == "variation-forte"


def test_a_search_that_improves_nothing_writes_no_version_and_records_the_incumbent(
    engine: Engine, tmp_path: Path
) -> None:
    measured = a_stubborn_market()

    outcome = improve.cycle_for(
        MARKET, measured=measured, output=tmp_path, engine=engine, ref=REF
    ).run(
        improve.escalation_for(measured),
        at=T0,
        incumbent=measured.baseline(),
        evidence=improve.evidence_context(measured, ref=REF),
    )
    assert outcome.status is CycleStatus.NO_IMPROVEMENT
    assert not list(tmp_path.glob("*.yaml")), "no version was invented"
    assert rows(engine) == [], "the chain itself records nothing when nothing improved"

    improve.record_baseline(engine, measured, comparisons=outcome.comparisons, at=T0, ref=REF)

    evidence = evidence_for(engine, MARKET)
    assert evidence is not None
    assert evidence.ref == REF
    assert evidence.comparisons == 2


def test_a_measurement_that_fails_is_counted_refused_and_journalled(
    engine: Engine, tmp_path: Path
) -> None:
    """A variant the campaign never measured cannot be silently skipped: it must be counted."""
    measured = a_fragile_market()
    broken = replace(measured, proposals=(_an_unmeasurable_spec(measured), *measured.proposals))

    outcome = improve.cycle_for(
        MARKET, measured=broken, output=tmp_path, engine=engine, ref=REF
    ).run(
        improve.escalation_for(broken),
        at=T0,
        incumbent=broken.baseline(),
        evidence=improve.evidence_context(broken, ref=REF),
    )

    failures = journal(engine, MEASURE_FAILED_EVENT)
    assert len(failures) == 1
    refused = [attempt for attempt in outcome.attempts if not attempt.accepted]
    assert any("could not be measured" in attempt.reason for attempt in refused)
    assert outcome.comparisons >= 2


def _an_unmeasurable_spec(measured: Any) -> CandidateSpec:
    """A proposal the campaign produced no report for: measuring it must raise, not lie."""
    return CandidateSpec(
        label="variation-sans-mesure",
        manifest=measured.incumbent.manifest,
        factory=measured.incumbent.factory,
        parameters={**PARAMETERS, "ema_fast": 99},
    )


# --- the refusals that must not be bypassed ------------------------------------------------------


def test_a_candidate_is_never_written_into_the_production_catalog(
    engine: Engine, tmp_path: Path
) -> None:
    """`write_candidate` refuses; the chain reports it instead of finding another way in."""
    measured = a_fragile_market()
    by_label = measured.by_label()
    variants = (Variant("variation-forte", dict(PARAMETERS), payload=None),)

    def search(candidates: object, evaluate: object) -> object:
        measured_value = evaluate(variants[0])  # type: ignore[operator]
        return _Outcome(measured_value)

    def build(market: str, supersedes: str, parameters: Mapping[str, float]) -> Any:
        return build_candidate(
            market=market,
            supersedes=supersedes,
            parameters=parameters,
            incumbent_manifest=improve.read_manifest(
                improve.STRATEGY_CONFIG_DIR / f"{supersedes}.yaml"
            ),
        )

    def write(candidate: Any) -> Path:
        return write_candidate(candidate, Path("config") / "strategies")

    cycle = ImprovementCycle(
        engine,
        propose=lambda evidence, escalation: variants,
        measure=lambda variant, evidence: improve.measurement_of(by_label[variant.label]),
        search=search,  # type: ignore[arg-type]
        build=build,
        write=write,
    )

    with pytest.raises(ValueError, match="refusing to write a candidate"):
        cycle.run(
            improve.escalation_for(measured),
            at=T0,
            incumbent=measured.baseline(),
            evidence=improve.evidence_context(measured, ref=REF),
        )

    assert [row.ref for row in rows(engine)] == [], "a refused write records no evidence"


class _Outcome:
    """The minimal shape the chain reads back from a search."""

    def __init__(self, measurement: Measurement) -> None:
        self._measurement = measurement
        self.accepted_label = "variation-forte"
        self.accepted_parameters = dict(PARAMETERS)

    @property
    def attempts(self) -> tuple[object, ...]:
        return (
            _Attempt(
                measurement=self._measurement,
                parameters=dict(PARAMETERS),
            ),
        )

    @property
    def trials(self) -> int:
        return 1

    @property
    def improved(self) -> bool:
        return True


class _Attempt:
    def __init__(self, *, measurement: Measurement, parameters: Mapping[str, float]) -> None:
        self.number = 1
        self.label = "variation-forte"
        self.parameters = parameters
        self.objective = measurement.objective
        self.metrics = dict(measurement.metrics)
        self.accepted = True
        self.reason = "retenue"


# --- the only place the real search meets the chain ---------------------------------------------


def test_the_real_search_is_the_one_that_decides(engine: Engine, tmp_path: Path) -> None:
    """The chain must not re-implement the acceptance rule; it must call it."""
    measured = a_fragile_market()
    by_label = measured.by_label()
    calls: list[str] = []

    def evaluate(candidate: Candidate) -> SearchMeasurement:
        calls.append(candidate.label)
        measured_value = improve.measurement_of(by_label[candidate.label])
        return SearchMeasurement(
            objective=measured_value.objective, metrics=dict(measured_value.metrics)
        )

    found = search_improvement(
        market=MARKET,
        incumbent=measured.baseline(),
        incumbent_label=REF,
        candidates=[
            Candidate(label=spec.label, parameters=dict(spec.parameters), payload=spec)
            for spec in measured.proposals
        ],
        evaluate=evaluate,
        guard=improve.risk_guard,
    )

    assert found.accepted_label == "variation-forte"
    assert found.trials == 2
    assert calls == ["variation-timide", "variation-forte"]


def test_a_drawdown_that_grows_is_refused_by_the_guard(engine: Engine, tmp_path: Path) -> None:
    """The guard is the one the script already had; a costly winner is not a winner."""
    measured = a_measured_market(
        [
            a_candidate(f"actuelle-{REF}", net=1000.0, score=0.4),
            a_candidate("variation-gourmande", net=5000.0, drawdown=400.0),
        ]
    )

    outcome = improve.cycle_for(
        MARKET, measured=measured, output=tmp_path, engine=engine, ref=REF
    ).run(
        improve.escalation_for(measured),
        at=T0,
        incumbent=measured.baseline(),
        evidence=improve.evidence_context(measured, ref=REF),
    )

    assert outcome.status is CycleStatus.NO_IMPROVEMENT
    assert any("drawdown" in attempt.reason for attempt in outcome.attempts)
