"""From an escalation to a candidate version: propose, measure, compare, stop at the door.

The operator asked for a chain, not four bricks. *"After a number of failures, or when the
same motif repeats, the AI proposes a strategy; it is backtested and compared with the current
strategy; if it is good, we adopt it and update."* Each step already existed:

* `ai.escalation` decides that a rewrite is justified and says why;
* `research.improvement` owns the acceptance rule — compare, refuse, keep the incumbent;
* `research.versioning` turns an accepted improvement into the next version and stops at the
  gate, refusing to write under `config/strategies/`;
* `ai.evidence` carries a measured result into `backtest_runs`, where the researcher reads it.

What none of them did was call the next one. This module is that sequence:

    escalation ──> evidence for that market (ai.evidence)
                      │  none? stop, say so, write nothing
                      ▼
                 variants (the AI's own proposals, or the caller's)
                      │  none measurable? stop, say so
                      ▼
                 measured one by one; a failure is journalled, never swallowed
                      │
                      ▼
                 compared with the version in place (injected: research.improvement)
                      │  no gain? the incumbent stays, and the count is written down
                      ▼
                 build_candidate + write_candidate  ──> a candidate manifest, never a promotion

**Two invariants hold this together.**

*The AI decides nothing.* Nothing here can create a signal, size a position or loosen a stop:
the only things written are a candidate manifest under the research directory, one measured run
in `backtest_runs`, and a journal entry. "Adopted" never means *loaded by the agent* —
promotion is the nine gates of §49, and `write_candidate` already refuses that path.

*The search is injected, and so is the measurement.* `search_improvement`, `build_candidate`
and `write_candidate` live in `research`, and the architecture forbids any production package
from importing `research` (`tests/test_architecture.py`: "backtest and research never load in
production"). So this module imports none of them: it takes them as callables and defines the
shapes it needs structurally. The concrete wiring lives in `scripts/backtest/improve.py`, and
the whole sequence is testable with a fake measurement and no market at all.

**One thing is not a formality.** The number of comparisons is carried out of the search, into
the measurement and into the journal. An accepted improvement that forgets how many variants it
beat is a false discovery waiting to happen: the multiple-testing correction needs that count,
and nobody can rebuild it afterwards.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import Engine

from tradingagent.ai.escalation import Escalation
from tradingagent.ai.evidence import (
    COMPARISONS_METRIC,
    OBJECTIVE_METRIC,
    MarketEvidence,
    MeasuredRun,
    evidence_for,
    journal,
    measured_number,
    record_run,
)
from tradingagent.ai.lab_store import LabStore, StoredProposal
from tradingagent.core.states import Severity

log = logging.getLogger(__name__)

#: The journal entry every run of the chain writes, outcome included — refusals too.
CYCLE_EVENT = "ai_improvement_cycle"
#: The journal entry for a variant that could not be measured. Loud, because a search that
#: loses its failures reports a smaller comparison count than it really made.
MEASURE_FAILED_EVENT = "ai_improvement_measure_failed"
#: How many attempts the journal keeps inline. The count is exact; the detail is bounded.
JOURNAL_ATTEMPTS = 24


@dataclass(frozen=True)
class Variant:
    """One proposed change, before it is measured.

    Structurally the `research.improvement.Candidate` the search expects: the label, the
    parameters to try, and why. `payload` carries whatever the caller attached — here, the id
    of the AI proposal the variant came from.
    """

    label: str
    parameters: Mapping[str, float]
    rationale: str = ""
    payload: Any = None


@dataclass(frozen=True)
class Measurement:
    """What one variant achieved, as the caller measured it. Same shape as the search's."""

    objective: float
    metrics: Mapping[str, float] = field(default_factory=dict)


class Comparison(Protocol):
    """One comparison made by the search, whatever its outcome."""

    @property
    def number(self) -> int: ...

    @property
    def label(self) -> str: ...

    @property
    def parameters(self) -> Mapping[str, float]: ...

    @property
    def objective(self) -> float: ...

    @property
    def metrics(self) -> Mapping[str, float]: ...

    @property
    def accepted(self) -> bool: ...

    @property
    def reason(self) -> str: ...


class SearchOutcome(Protocol):
    """What an injected search returns. `research.improvement.ImprovementOutcome` fits."""

    @property
    def attempts(self) -> Sequence[Comparison]: ...

    @property
    def accepted_label(self) -> str | None: ...

    @property
    def accepted_parameters(self) -> Mapping[str, float] | None: ...

    @property
    def trials(self) -> int: ...

    @property
    def improved(self) -> bool: ...


class CandidateVersion(Protocol):
    """The version an accepted improvement produces. `research.versioning.CandidateVersion`."""

    @property
    def ref(self) -> str: ...

    @property
    def supersedes(self) -> str: ...

    @property
    def parameters(self) -> Mapping[str, float]: ...


class ImprovementRunner(Protocol):
    """What the daily pass needs from a chain: one method, one escalation, one outcome."""

    def run(
        self,
        escalation: Escalation,
        *,
        at: datetime,
        incumbent: "Measurement | None" = None,
        evidence: "MarketEvidence | None" = None,
    ) -> "CycleOutcome": ...


Search = Callable[[Sequence[Variant], Callable[[Variant], Measurement]], SearchOutcome]
Propose = Callable[[MarketEvidence, Escalation], Sequence[Variant]]
Measure = Callable[[Variant, MarketEvidence], Measurement]
Build = Callable[[str, str, Mapping[str, float]], CandidateVersion]
Write = Callable[[CandidateVersion], Path]


class CycleStatus(StrEnum):
    """Where the chain stopped, and why. A stop is a result, not a failure."""

    SKIPPED_NO_EVIDENCE = "skipped_no_evidence"
    SKIPPED_WITHOUT_OBJECTIVE = "skipped_without_objective"
    NO_VARIANTS = "no_variants"
    NO_IMPROVEMENT = "no_improvement"
    IMPROVED = "improved"


@dataclass(frozen=True)
class CycleOutcome:
    """The whole chain for one escalation: where it stopped, and what it produced."""

    market: str
    status: CycleStatus
    reason: str
    ref: str | None = None
    incumbent_objective: float | None = None
    comparisons: int = 0
    attempts: tuple[Comparison, ...] = ()
    accepted_label: str | None = None
    candidate_ref: str | None = None
    candidate_path: Path | None = None
    #: True when the accepted candidate's measurement was stored as evidence. A chain that
    #: measured without recording — no database, or nothing measurable — says so here rather
    #: than letting a caller believe the next pass will find proof it never wrote.
    recorded: bool = False
    #: The search itself, for a caller that wants to render the whole comparison table
    #: (`research.improvement.report`). Nothing in this module reads it.
    search: SearchOutcome | None = None

    @property
    def improved(self) -> bool:
        return self.status is CycleStatus.IMPROVED

    @property
    def relative_gain(self) -> float:
        """The accepted gain over the version in place; `0.0` when there is none to report."""
        if not self.improved or not self.incumbent_objective:
            return 0.0
        accepted = next((attempt for attempt in self.attempts if attempt.accepted), None)
        if accepted is None:
            return 0.0
        return (accepted.objective - self.incumbent_objective) / abs(self.incumbent_objective)

    def message(self) -> str:
        """The sentence the operator reads. It never says a strategy was adopted."""
        head = f"🧪 {self.market} — "
        if self.status is CycleStatus.IMPROVED:
            gain = f"+{self.relative_gain * 100:.1f} % " if self.incumbent_objective else ""
            return (
                f"{head}variante {self.accepted_label} retenue {gain}après "
                f"{self.comparisons} comparaison(s). Candidat {self.candidate_ref} écrit ; "
                "il doit franchir les neuf portes avant toute promotion. "
                f"{self.comparisons} essai(s) entrent dans la correction du test multiple."
            )
        if self.status is CycleStatus.NO_IMPROVEMENT:
            return (
                f"{head}aucune variante ne bat {self.ref} après {self.comparisons} "
                "comparaison(s) : la version en place reste. "
                f"{self.comparisons} essai(s) entrent dans la correction du test multiple."
            )
        return f"{head}{self.reason}"


class ImprovementCycle:
    """Runs the chain for one escalation, with every outside dependency injected.

    The engine is optional. With one — always, in the daily pass — every run and every refusal
    is journalled and the accepted measurement is recorded as evidence. Without one, a research
    run can still measure and compare, but nothing is persisted and the outcome says so
    (`recorded=False`): a chain that quietly forgot the evidence would be the very defect this
    module exists to fix.
    """

    def __init__(
        self,
        engine: Engine | None,
        *,
        propose: Propose,
        measure: Measure,
        search: Search,
        build: Build,
        write: Write,
    ) -> None:
        self._engine = engine
        self._propose = propose
        self._measure = measure
        self._search = search
        self._build = build
        self._write = write

    def run(
        self,
        escalation: Escalation,
        *,
        at: datetime,
        incumbent: Measurement | None = None,
        evidence: MarketEvidence | None = None,
    ) -> CycleOutcome:
        """The whole sequence for one escalation, stopping cleanly wherever it must.

        `incumbent` lets a caller that just measured the version in place — a campaign, on the
        same dataset — pass that measurement in. Without it, the baseline is the one recorded
        in `backtest_runs`; with neither, the chain stops and says so rather than inventing a
        baseline to compare against.

        `evidence` is the context of that fresh measurement: the frozen dataset, the window and
        the costs it was taken under. A caller that measured outside the database hands it in,
        so the chain can record the candidate's run against the very same dataset instead of
        looking for a context that is not there yet.
        """
        market = escalation.market
        context = evidence
        if context is None and self._engine is not None:
            context = evidence_for(self._engine, market)
        if context is None:
            return self._finish(
                CycleOutcome(
                    market=market,
                    status=CycleStatus.SKIPPED_NO_EVIDENCE,
                    reason=(
                        f"aucune preuve mesurée pour {market} : aucun backtest enregistré, "
                        "l'enchaînement s'arrête sans inventer de référence"
                    ),
                ),
                at=at,
            )
        baseline = incumbent if incumbent is not None else self._recorded_baseline(context)
        if baseline is None:
            return self._finish(
                CycleOutcome(
                    market=market,
                    ref=context.ref,
                    status=CycleStatus.SKIPPED_WITHOUT_OBJECTIVE,
                    reason=(
                        f"la mesure enregistrée pour {market} ne déclare pas son objectif : "
                        "aucune comparaison n'est possible"
                    ),
                ),
                at=at,
            )
        variants = tuple(self._propose(context, escalation))
        if not variants:
            return self._finish(
                CycleOutcome(
                    market=market,
                    ref=context.ref,
                    status=CycleStatus.NO_VARIANTS,
                    reason=f"aucune variante exploitable à mesurer pour {market}",
                    incumbent_objective=baseline.objective,
                ),
                at=at,
            )

        found = self._search(variants, lambda variant: self._evaluated(variant, context, at))
        attempts = tuple(found.attempts)
        if not found.improved:
            return self._finish(
                CycleOutcome(
                    market=market,
                    ref=context.ref,
                    status=CycleStatus.NO_IMPROVEMENT,
                    reason=(
                        f"{found.trials} comparaison(s) sans amélioration : "
                        f"{context.ref} reste en place"
                    ),
                    incumbent_objective=baseline.objective,
                    comparisons=found.trials,
                    attempts=attempts,
                    search=found,
                ),
                at=at,
            )
        return self._finish(
            self._adopt(context, found, baseline, at=at),
            at=at,
        )

    # -- the steps -------------------------------------------------------------------------

    def _evaluated(self, variant: Variant, evidence: MarketEvidence, at: datetime) -> Measurement:
        """Measure one variant, journalling a failure before letting the search record it.

        The search turns a measurement error into a refused attempt with its reason, which is
        the right place for it; the journal adds the durable trace, because a failure that
        only exists inside a returned value disappears with the process.
        """
        try:
            return self._measure(variant, evidence)
        except Exception as error:
            if self._engine is not None:
                journal(
                    self._engine,
                    kind=MEASURE_FAILED_EVENT,
                    severity=Severity.WARNING,
                    detail={
                        "market": evidence.market,
                        "ref": evidence.ref,
                        "label": variant.label,
                        "parameters": {
                            key: float(value) for key, value in variant.parameters.items()
                        },
                        "error": f"{type(error).__name__}: {error}",
                    },
                    at=at,
                    symbol=evidence.market,
                )
            log.warning("improvement cycle: %s could not be measured: %s", variant.label, error)
            raise

    def _adopt(
        self,
        evidence: MarketEvidence,
        found: SearchOutcome,
        baseline: Measurement,
        *,
        at: datetime,
    ) -> CycleOutcome:
        """Build and write the candidate, then record its measurement as the new evidence."""
        parameters = dict(found.accepted_parameters or {})
        candidate = self._build(evidence.market, evidence.ref, parameters)
        path = self._write(candidate)
        recorded, note = self._record_candidate(evidence, candidate, found, path=path, at=at)
        return CycleOutcome(
            market=evidence.market,
            ref=evidence.ref,
            status=CycleStatus.IMPROVED,
            reason=(
                f"{found.accepted_label} retenue après {found.trials} comparaison(s) : "
                f"candidat {candidate.ref} écrit en {path}{note}"
            ),
            incumbent_objective=baseline.objective,
            comparisons=found.trials,
            attempts=tuple(found.attempts),
            accepted_label=found.accepted_label,
            candidate_ref=candidate.ref,
            candidate_path=path,
            recorded=recorded,
            search=found,
        )

    def _record_candidate(
        self,
        evidence: MarketEvidence,
        candidate: CandidateVersion,
        found: SearchOutcome,
        *,
        path: Path,
        at: datetime,
    ) -> tuple[bool, str]:
        """Store the accepted measurement as the evidence of the version under consideration.

        The next pass reads the newest run for the market, so the candidate's own figures —
        not a stale baseline — become what the researcher reasons about. The comparison count
        goes in with them: the two are one measurement.
        """
        accepted = next((attempt for attempt in found.attempts if attempt.accepted), None)
        if accepted is None:
            return False, " ; l'amélioration ne porte aucun essai retenu, preuve non enregistrée"
        if self._engine is None:
            return False, " ; preuve non enregistrée (aucune base de données)"
        try:
            record_run(
                self._engine,
                MeasuredRun(
                    market=evidence.market,
                    ref=candidate.ref,
                    dataset_id=evidence.dataset_id,
                    fingerprint=evidence.fingerprint,
                    window_start=evidence.window_start,
                    window_end=evidence.window_end,
                    objective=accepted.objective,
                    metrics=dict(accepted.metrics),
                    costs=dict(evidence.costs),
                    comparisons=found.trials,
                    report_path=str(path),
                ),
                at=at,
            )
        except ValueError as error:
            # A measurement with nothing measurable in it cannot become evidence: better said
            # loudly than stored as an empty row the researcher would read as a result.
            log.error(
                "improvement cycle: %s was not recorded as evidence: %s", candidate.ref, error
            )
            return False, f" ; preuve non enregistrée ({error})"
        return True, ""

    def _recorded_baseline(self, evidence: MarketEvidence) -> Measurement | None:
        if evidence.objective is None:
            return None
        metrics = {
            key: value
            for key, value in evidence.metrics.items()
            if key not in (OBJECTIVE_METRIC, COMPARISONS_METRIC)
        }
        return Measurement(objective=evidence.objective, metrics=metrics)

    def _finish(self, outcome: CycleOutcome, *, at: datetime) -> CycleOutcome:
        """Write the run to the journal, then hand it back. Every stop leaves a trace."""
        if self._engine is not None:
            journal(
                self._engine,
                kind=CYCLE_EVENT,
                severity=Severity.INFO,
                detail=_journal_detail(outcome),
                at=at,
                symbol=outcome.market,
            )
        log.info("improvement cycle %s: %s", outcome.market, outcome.reason)
        return outcome


def _journal_detail(outcome: CycleOutcome) -> dict[str, Any]:
    """The run as JSON. A number that was not measured is absent, never zero."""
    attempts: list[dict[str, Any]] = []
    for attempt in outcome.attempts[:JOURNAL_ATTEMPTS]:
        entry: dict[str, Any] = {
            "number": attempt.number,
            "label": attempt.label,
            "accepted": bool(attempt.accepted),
            "reason": attempt.reason,
        }
        objective = measured_number(attempt.objective)
        if objective is not None:
            entry["objective"] = objective
        attempts.append(entry)
    return {
        "market": outcome.market,
        "ref": outcome.ref,
        "status": outcome.status.value,
        "reason": outcome.reason,
        "comparisons": outcome.comparisons,
        "attempts_made": len(outcome.attempts),
        "accepted_label": outcome.accepted_label,
        "candidate_ref": outcome.candidate_ref,
        "candidate_path": None if outcome.candidate_path is None else str(outcome.candidate_path),
        "incumbent_objective": outcome.incumbent_objective,
        "candidate_recorded": outcome.recorded,
        "attempts": attempts,
    }


def propose_from_lab(store: LabStore) -> Propose:
    """The AI's own open proposals as the source of variants, for a given market.

    This is the production proposal source: the researcher already stored what it thinks
    should change, with the figures that motivated it; the chain measures those, and only
    those, against the version in place. Reading proposals rather than inventing variants is
    what keeps "the AI proposes" true to the operator's words.
    """

    def propose(evidence: MarketEvidence, escalation: Escalation) -> Sequence[Variant]:
        open_proposals = [
            proposal for proposal in store.open_proposals() if proposal.market == evidence.market
        ]
        return variants_from_proposals(open_proposals, base_parameters=evidence.parameters)

    return propose


def variants_from_proposals(
    proposals: Sequence[StoredProposal], *, base_parameters: Mapping[str, float]
) -> tuple[Variant, ...]:
    """The AI's stored proposals, as variants that can actually be measured.

    This is where "the AI proposes a strategy" becomes something a backtest can answer. A
    proposal is kept only when it names a parameter the incumbent really has, and gives it a
    different numeric value: a `freeze` proposes the value it already has, an `enable` proposes
    a boolean, and neither is a variant — measuring them would mean inventing a change. The
    same variant proposed twice is kept once, because measuring it twice would inflate the
    comparison count the multiple-testing correction depends on.
    """
    variants: list[Variant] = []
    seen: set[str] = set()
    for proposal in proposals:
        change = proposal.proposed_change
        name = change.get("parameter")
        if not isinstance(name, str) or name not in base_parameters:
            continue
        value = measured_number(change.get("proposed_value"))
        if value is None:
            continue
        if value == measured_number(base_parameters[name]):
            continue
        moved: dict[str, float] = {}
        for key, raw in base_parameters.items():
            number = measured_number(raw)
            if number is not None:
                moved[str(key)] = number
        moved[name] = value
        label = f"{name}={value:g}"
        if label in seen:
            continue
        seen.add(label)
        variants.append(
            Variant(
                label=label,
                parameters=moved,
                rationale=proposal.hypothesis,
                payload=proposal.id,
            )
        )
    return tuple(variants)


__all__ = [
    "CYCLE_EVENT",
    "JOURNAL_ATTEMPTS",
    "MEASURE_FAILED_EVENT",
    "CandidateVersion",
    "Comparison",
    "CycleOutcome",
    "CycleStatus",
    "ImprovementCycle",
    "ImprovementRunner",
    "Measurement",
    "SearchOutcome",
    "Variant",
    "propose_from_lab",
    "variants_from_proposals",
]
