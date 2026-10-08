"""The chain from an escalation to a candidate version, measured and compared.

The operator's request has four beats: failures repeat, the AI proposes, the proposal is
backtested against the version in place, and only a real improvement becomes a new version.
`escalation.py` answers the first, `research.improvement` the third and `research.versioning`
the fourth; this module is the sequence that joins them, and these tests pin its edges:

* with proof, a variant that wins produces a candidate — never a promotion;
* without proof it stops, says so, and invents no baseline;
* a measurement that fails is written down instead of being swallowed;
* the number of comparisons comes back out, because the multiple-testing correction needs it.

The search is injected, so the sequence is tested here without a market; the real
`search_improvement`/`build_candidate` wiring is exercised in `tests/backtest`.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, insert, select
from sqlalchemy.orm import Session

from tradingagent.ai.escalation import Escalation, FailurePattern
from tradingagent.ai.evidence import MeasuredRun, record_run
from tradingagent.ai.improvement_cycle import (
    CYCLE_EVENT,
    MEASURE_FAILED_EVENT,
    CycleStatus,
    ImprovementCycle,
    Measurement,
    Variant,
    variants_from_proposals,
)
from tradingagent.ai.lab_store import AnalysisRecord, LabStore, ProposalRecord, StoredProposal
from tradingagent.core.states import AnalysisKind, Severity
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import BacktestRunRow, StrategyVersionRow, SystemEventRow

T0 = datetime(2026, 10, 8, 6, 0, tzinfo=UTC)
MARKET = "frxXAUUSD"
REF = "witness@1.1.0"
PARAMETERS = {"ema_fast": 20.0, "ema_slow": 50.0, "take_profit_rr": 2.0}


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'cycle.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


def an_escalation(market: str = MARKET) -> Escalation:
    pattern = FailurePattern(
        market=market,
        kind="recurring_pattern",
        occurrences=3,
        first_seen=T0 - timedelta(days=5),
        last_seen=T0,
        reasons=("le motif se répète",),
    )
    return Escalation(market=market, pattern=pattern, trigger=3, reason="le motif se répète 3 fois")


def record_incumbent(
    engine: Engine, *, objective: float = 1000.0, market: str = MARKET, ref: str = REF
) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(StrategyVersionRow).values(
                ref=ref,
                strategy_id=ref.partition("@")[0],
                version=ref.partition("@")[2],
                manifest={"parameters": dict(PARAMETERS)},
                content_hash="c" * 64,
                first_seen_at=T0 - timedelta(days=60),
            )
        )
    record_run(
        engine,
        MeasuredRun(
            market=market,
            ref=ref,
            dataset_id="xau-m15-2026",
            fingerprint="a" * 64,
            window_start=T0 - timedelta(days=30),
            window_end=T0,
            objective=objective,
            metrics={"max_drawdown_eur": 90.0, "trades": 120.0},
            costs={"spread": 0.3},
            comparisons=2,
        ),
        at=T0 - timedelta(days=1),
    )


@dataclass(frozen=True)
class Attempted:
    number: int
    label: str
    parameters: Mapping[str, float]
    objective: float
    metrics: Mapping[str, float]
    accepted: bool
    reason: str
    rationale: str = ""


@dataclass(frozen=True)
class Outcome:
    attempts: tuple[Attempted, ...]
    accepted_label: str | None
    accepted_parameters: Mapping[str, float] | None
    accepted_payload: Any = None

    @property
    def trials(self) -> int:
        return len(self.attempts)

    @property
    def improved(self) -> bool:
        return self.accepted_label is not None


def scripted_search(*, accept: str | None) -> Any:
    """A stand-in for `search_improvement`: same shape, no market, no arithmetic."""

    def search(variants: Sequence[Variant], evaluate: Any) -> Outcome:
        attempts: list[Attempted] = []
        accepted_parameters: Mapping[str, float] | None = None
        for number, variant in enumerate(variants, start=1):
            try:
                measured = evaluate(variant)
            except Exception as error:  # exactly what `search_improvement` records
                attempts.append(
                    Attempted(
                        number=number,
                        label=variant.label,
                        parameters=dict(variant.parameters),
                        objective=float("-inf"),
                        metrics={},
                        accepted=False,
                        reason=f"could not be measured: {type(error).__name__}: {error}",
                    )
                )
                continue
            won = variant.label == accept
            attempts.append(
                Attempted(
                    number=number,
                    label=variant.label,
                    parameters=dict(variant.parameters),
                    objective=measured.objective,
                    metrics=dict(measured.metrics),
                    accepted=won,
                    reason="retenue" if won else "écartée",
                )
            )
            if won:
                accepted_parameters = dict(variant.parameters)
        accepted = accept if accepted_parameters is not None else None
        return Outcome(tuple(attempts), accepted, accepted_parameters)

    return search


@dataclass(frozen=True)
class Candidate:
    ref: str
    supersedes: str
    parameters: Mapping[str, float]


def a_cycle(
    engine: Engine,
    *,
    accept: str | None,
    measures: Mapping[str, float] | None = None,
    fails: Sequence[str] = (),
    written: list[Candidate] | None = None,
    measured: list[str] | None = None,
    proposed: Sequence[Variant] | None = None,
    target: Path | None = None,
) -> ImprovementCycle:
    objectives = dict(measures or {"pousse-rr": 1300.0, "baisse-rr": 900.0})

    def propose(evidence: Any, escalation: Escalation) -> Sequence[Variant]:
        if proposed is not None:
            return proposed
        return tuple(
            Variant(
                label=label,
                parameters={**PARAMETERS, "take_profit_rr": 2.5},
                rationale=label,
            )
            for label in objectives
        )

    def measure(variant: Variant, evidence: Any) -> Measurement:
        if measured is not None:
            measured.append(variant.label)
        if variant.label in fails:
            raise RuntimeError("le jeu de données est illisible")
        return Measurement(
            objective=objectives.get(variant.label, 0.0),
            metrics={"max_drawdown_eur": 80.0, "trades": 110.0},
        )

    def build(market: str, supersedes: str, parameters: Mapping[str, float]) -> Candidate:
        return Candidate(
            ref=f"{supersedes.partition('@')[0]}@1.1.1",
            supersedes=supersedes,
            parameters=dict(parameters),
        )

    def write(candidate: Any) -> Path:
        if written is not None:
            written.append(candidate)
        directory = target or Path("docs") / "research" / "candidates"
        return directory / f"{candidate.ref}.yaml"

    return ImprovementCycle(
        engine,
        propose=propose,
        measure=measure,
        search=scripted_search(accept=accept),
        build=build,
        write=write,
    )


def runs(engine: Engine) -> list[BacktestRunRow]:
    with Session(engine) as session:
        return list(session.scalars(select(BacktestRunRow).order_by(BacktestRunRow.id)).all())


def journal(engine: Engine, kind: str = CYCLE_EVENT) -> list[SystemEventRow]:
    with Session(engine) as session:
        return list(
            session.scalars(
                select(SystemEventRow)
                .where(SystemEventRow.kind == kind)
                .order_by(SystemEventRow.id)
            ).all()
        )


# --- an escalation with proof ---------------------------------------------------------------


def test_a_winning_variant_measures_compares_and_produces_a_candidate(
    engine: Engine, tmp_path: Path
) -> None:
    record_incumbent(engine)
    written: list[Candidate] = []

    outcome = a_cycle(engine, accept="pousse-rr", written=written, target=tmp_path).run(
        an_escalation(), at=T0
    )

    assert outcome.status is CycleStatus.IMPROVED
    assert outcome.candidate_ref == "witness@1.1.1"
    assert outcome.candidate_path == tmp_path / "witness@1.1.1.yaml"
    assert outcome.accepted_label == "pousse-rr"
    assert written and written[0].supersedes == REF
    assert written[0].parameters["take_profit_rr"] == 2.5


def test_the_accepted_measurement_becomes_the_evidence_of_the_new_version(
    engine: Engine, tmp_path: Path
) -> None:
    """The bridge closes the loop: the next pass reads the candidate's own measurement."""
    record_incumbent(engine)

    a_cycle(engine, accept="pousse-rr", target=tmp_path).run(an_escalation(), at=T0)

    stored = runs(engine)[-1]
    assert stored.ref == "witness@1.1.1"
    assert stored.metrics["objective"] == 1300.0
    assert stored.metrics["comparisons"] == 2
    assert stored.metrics["max_drawdown_eur"] == 80.0


def test_the_comparison_count_comes_back_out(engine: Engine, tmp_path: Path) -> None:
    """An accepted improvement without its number of trials is a false discovery waiting."""
    record_incumbent(engine)

    outcome = a_cycle(engine, accept="pousse-rr", target=tmp_path).run(an_escalation(), at=T0)

    assert outcome.comparisons == 2
    assert "2 comparaison" in outcome.message()
    assert "test multiple" in outcome.message()


def test_a_search_that_improves_nothing_leaves_the_incumbent_in_place(
    engine: Engine, tmp_path: Path
) -> None:
    record_incumbent(engine)
    written: list[Candidate] = []

    outcome = a_cycle(engine, accept=None, written=written, target=tmp_path).run(
        an_escalation(), at=T0
    )

    assert outcome.status is CycleStatus.NO_IMPROVEMENT
    assert outcome.candidate_ref is None
    assert written == []
    assert [row.ref for row in runs(engine)] == [REF], "no version was invented"


def test_the_incumbent_is_compared_against_its_own_recorded_measurement(
    engine: Engine, tmp_path: Path
) -> None:
    """The baseline is read from the bridge, not guessed: 1000 EUR is what was measured."""
    record_incumbent(engine, objective=1000.0)

    outcome = a_cycle(engine, accept="pousse-rr", target=tmp_path).run(an_escalation(), at=T0)

    assert outcome.incumbent_objective == 1000.0
    assert outcome.relative_gain == pytest.approx(0.3)
    assert "+30.0 %" in outcome.message()


# --- an escalation without proof --------------------------------------------------------------


def test_without_any_proof_the_chain_stops_and_says_so(engine: Engine, tmp_path: Path) -> None:
    """Today's case: `backtest_runs` is empty, so the researcher must propose nothing."""
    written: list[Candidate] = []
    measured: list[str] = []

    outcome = a_cycle(
        engine, accept="pousse-rr", written=written, measured=measured, target=tmp_path
    ).run(an_escalation(), at=T0)

    assert outcome.status is CycleStatus.SKIPPED_NO_EVIDENCE
    assert outcome.comparisons == 0
    assert measured == [], "nothing is measured without a baseline to compare it to"
    assert written == []
    assert runs(engine) == []
    assert "aucune preuve" in outcome.reason
    assert "aucune preuve" in outcome.message()


def test_the_stop_is_written_down(engine: Engine, tmp_path: Path) -> None:
    a_cycle(engine, accept=None, target=tmp_path).run(an_escalation(), at=T0)

    entries = journal(engine)
    assert len(entries) == 1
    assert entries[0].detail["market"] == MARKET
    assert entries[0].detail["status"] == CycleStatus.SKIPPED_NO_EVIDENCE.value
    assert entries[0].symbol == MARKET


def test_an_escalation_without_a_usable_baseline_stops_too(engine: Engine, tmp_path: Path) -> None:
    """A run that never declared what it maximises cannot be a baseline, and is not patched."""
    with engine.begin() as connection:
        connection.execute(
            insert(BacktestRunRow).values(
                ref=REF,
                market=MARKET,
                dataset_id="xau-m15-2026",
                fingerprint="a" * 64,
                window_start=T0 - timedelta(days=30),
                window_end=T0,
                metrics={"trades": 120.0},
                costs={},
                report_path=None,
                created_at=T0 - timedelta(days=1),
            )
        )
    written: list[Candidate] = []

    outcome = a_cycle(engine, accept="pousse-rr", written=written, target=tmp_path).run(
        an_escalation(), at=T0
    )

    assert outcome.status is CycleStatus.SKIPPED_WITHOUT_OBJECTIVE
    assert written == []
    assert "objectif" in outcome.reason


def test_no_variant_to_measure_is_reported_not_treated_as_an_improvement(
    engine: Engine, tmp_path: Path
) -> None:
    record_incumbent(engine)

    outcome = a_cycle(engine, accept=None, proposed=(), target=tmp_path).run(an_escalation(), at=T0)

    assert outcome.status is CycleStatus.NO_VARIANTS
    assert outcome.comparisons == 0
    assert "variante" in outcome.reason


# --- a measurement that fails ------------------------------------------------------------------


def test_a_failed_measurement_is_recorded_and_not_swallowed(engine: Engine, tmp_path: Path) -> None:
    record_incumbent(engine)

    outcome = a_cycle(engine, accept=None, fails=("baisse-rr",), target=tmp_path).run(
        an_escalation(), at=T0
    )

    refused = [attempt for attempt in outcome.attempts if not attempt.accepted]
    assert outcome.comparisons == 2, "the failed attempt still counted as an attempt"
    assert any("illisible" in attempt.reason for attempt in refused)
    failures = journal(engine, MEASURE_FAILED_EVENT)
    assert len(failures) == 1
    assert failures[0].severity is Severity.WARNING
    assert failures[0].detail["label"] == "baisse-rr"
    assert "illisible" in failures[0].detail["error"]


def test_a_failed_measurement_does_not_produce_a_candidate(engine: Engine, tmp_path: Path) -> None:
    record_incumbent(engine)
    written: list[Candidate] = []

    outcome = a_cycle(engine, accept="pousse-rr", fails=("pousse-rr",), written=written).run(
        an_escalation(), at=T0
    )

    assert outcome.status is CycleStatus.NO_IMPROVEMENT
    assert written == []


# --- the AI's own proposals become the variants ------------------------------------------------


def stored_proposals(engine: Engine, changes: Sequence[Mapping[str, Any]]) -> list[StoredProposal]:
    store = LabStore(engine)
    for change in changes:
        store.record_analysis(
            AnalysisRecord(
                kind=AnalysisKind.HYPOTHESIS,
                market=MARKET,
                ref=REF,
                model="deterministic",
                request={},
                findings={"hypotheses": [dict(change)]},
                created_at=T0,
            )
        )
        store.record_proposal(
            ProposalRecord(
                market=MARKET,
                hypothesis="hypothèse déterministe",
                proposed_change=dict(change),
                created_at=T0,
                ref=REF,
            )
        )
    return list(store.open_proposals())


def a_change(
    parameter: str, current: Any, proposed: Any, action: str = "decrease"
) -> dict[str, Any]:
    return {
        "parameter": parameter,
        "current_value": current,
        "proposed_value": proposed,
        "action": action,
        "evidence": ["backtest_runs:witness@1.1.0#x=1"],
        "falsification": "sinon l'hypothèse est réfutée",
        "validation": ["walk_forward"],
    }


def test_a_numeric_proposal_becomes_a_variant_of_the_incumbent(engine: Engine) -> None:
    stored = stored_proposals(engine, [a_change("take_profit_rr", 2.0, 1.5)])

    variants = variants_from_proposals(stored, base_parameters=PARAMETERS)

    assert len(variants) == 1
    assert variants[0].parameters == {**PARAMETERS, "take_profit_rr": 1.5}
    assert variants[0].rationale == "hypothèse déterministe"
    assert variants[0].payload == stored[0].id


def test_a_proposal_that_changes_no_value_is_not_a_variant(engine: Engine) -> None:
    """`freeze` keeps the value: there is nothing to measure against the incumbent."""
    stored = stored_proposals(engine, [a_change("ema_slow", 50.0, 50.0, action="freeze")])

    assert variants_from_proposals(stored, base_parameters=PARAMETERS) == ()


def test_a_proposal_about_an_unknown_parameter_is_not_measurable(engine: Engine) -> None:
    stored = stored_proposals(engine, [a_change("regime_filter", False, True, action="enable")])

    assert variants_from_proposals(stored, base_parameters=PARAMETERS) == ()


def test_a_non_numeric_proposal_is_left_for_a_human(engine: Engine) -> None:
    stored = stored_proposals(engine, [a_change("take_profit_rr", 2.0, "beaucoup")])

    assert variants_from_proposals(stored, base_parameters=PARAMETERS) == ()
