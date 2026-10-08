"""The daily pass runs the improvement chain — but only when there is a measurement.

Two things are being pinned here.

First, the *sequence*: after the escalations, and after the researcher has proposed, the pass
hands each escalation to the chain. A pass with no chain behaves exactly as before, which is
why every existing test of the lab keeps passing.

Second, the *stop*: with an empty `backtest_runs` — today's case — the chain stops, says
"aucune preuve", writes nothing and invents no baseline. The pass reports it instead of
failing, because "I have no measurement yet" is information, not an incident.

The bridge is verified from the consumer's side: `DailyLab._evidence` reads back a run that a
campaign recorded through `ai.evidence`.
"""

import asyncio
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, insert, select
from sqlalchemy.orm import Session

from tradingagent.ai.analyst import LossKind, TradeAnalyst
from tradingagent.ai.daily import ClosedTradeFact, DailyLab
from tradingagent.ai.escalation import Escalation
from tradingagent.ai.evidence import MeasuredRun, record_gate, record_run
from tradingagent.ai.improvement_cycle import (
    CYCLE_EVENT,
    CycleOutcome,
    CycleStatus,
    ImprovementCycle,
    Measurement,
    Variant,
    propose_from_lab,
)
from tradingagent.ai.lab_store import AnalysisRecord, LabStore, ProposalRecord
from tradingagent.ai.researcher import BacktestEvidence, StrategyResearcher
from tradingagent.analytics.model import Trade
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import AnalysisKind, ProposalStatus, ValidationStage
from tradingagent.core.timeframe import Timeframe
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import AiProposalRow, StrategyVersionRow, SystemEventRow

NOW = datetime(2026, 10, 8, 22, 0, tzinfo=UTC)
MARKET = "frxXAUUSD"
REF = "witness@1.1.0"
PARAMETERS = {"ema_fast": 20.0, "take_profit_rr": 2.0}


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'daily.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str, *, parse_mode: str | None = None) -> bool:
        self.sent.append(text)
        return True


class RecordingCycle:
    """A chain that records how it was called, and answers with a canned outcome."""

    def __init__(self, outcome: CycleOutcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple[Escalation, datetime, Measurement | None]] = []

    def run(
        self,
        escalation: Escalation,
        *,
        at: datetime,
        incumbent: Measurement | None = None,
        evidence: Any = None,
    ) -> CycleOutcome:
        self.calls.append((escalation, at, incumbent))
        return self.outcome


def a_failure_history(engine: Engine, times: int = 3, market: str = MARKET) -> None:
    store = LabStore(engine)
    for day in range(times):
        store.record_analysis(
            AnalysisRecord(
                kind=AnalysisKind.LOSS_ANALYSIS,
                market=market,
                ref=REF,
                model="deterministic",
                request={},
                findings={
                    "kind": LossKind.RECURRING_PATTERN.value,
                    "reason": "le motif se répète",
                },
                created_at=NOW - timedelta(days=day),
            )
        )


def record_incumbent(engine: Engine, *, objective: float = 1000.0, market: str = MARKET) -> None:
    record_run(
        engine,
        MeasuredRun(
            market=market,
            ref=REF,
            dataset_id="xau-m15-2026",
            fingerprint="a" * 64,
            window_start=NOW - timedelta(days=30),
            window_end=NOW,
            objective=objective,
            metrics={"max_drawdown_eur": 90.0, "trades": 120.0},
            costs={"spread": 0.3},
        ),
        at=NOW - timedelta(days=1),
    )


def build_lab(
    engine: Engine,
    *,
    cycle: Any = None,
    notifier: FakeNotifier | None = None,
) -> DailyLab:
    store = LabStore(engine)
    return DailyLab(
        engine,
        analyst=TradeAnalyst(store),
        researcher=None,
        notifier=notifier,
        cycle=cycle,
        now=lambda: NOW,
    )


def a_real_cycle(
    engine: Engine,
    *,
    objectives: Mapping[str, float],
    accept: str | None,
    written: list[Any] | None = None,
    target: Path | None = None,
) -> ImprovementCycle:
    """The production proposal source, with the market-facing parts injected."""

    def measure(variant: Variant, evidence: Any) -> Measurement:
        return Measurement(
            objective=objectives[variant.label],
            metrics={"max_drawdown_eur": 70.0, "trades": 100.0},
        )

    def search(variants: Sequence[Variant], evaluate: Any) -> Any:
        attempts = []
        accepted_parameters = None
        for number, variant in enumerate(variants, start=1):
            measured = evaluate(variant)
            won = variant.label == accept
            attempts.append(
                _Attempt(
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
        return _Outcome(
            tuple(attempts), accept if accepted_parameters else None, accepted_parameters
        )

    def build(market: str, supersedes: str, parameters: Mapping[str, float]) -> Any:
        return _Candidate(ref="witness@1.1.1", supersedes=supersedes, parameters=dict(parameters))

    def write(candidate: Any) -> Path:
        if written is not None:
            written.append(candidate)
        directory = target or Path("docs") / "research" / "candidates"
        return directory / f"{candidate.ref}.yaml"

    return ImprovementCycle(
        engine,
        propose=propose_from_lab(LabStore(engine)),
        measure=measure,
        search=search,
        build=build,
        write=write,
    )


class _Attempt:
    def __init__(
        self,
        *,
        number: int,
        label: str,
        parameters: Mapping[str, float],
        objective: float,
        metrics: Mapping[str, float],
        accepted: bool,
        reason: str,
    ) -> None:
        self.number, self.label, self.parameters = number, label, parameters
        self.objective, self.metrics, self.accepted, self.reason = (
            objective,
            metrics,
            accepted,
            reason,
        )


class _Outcome:
    def __init__(
        self,
        attempts: tuple[_Attempt, ...],
        accepted_label: str | None,
        accepted_parameters: Mapping[str, float] | None,
    ) -> None:
        self.attempts, self.accepted_label = attempts, accepted_label
        self.accepted_parameters = accepted_parameters

    @property
    def trials(self) -> int:
        return len(self.attempts)

    @property
    def improved(self) -> bool:
        return self.accepted_label is not None


class _Candidate:
    def __init__(self, *, ref: str, supersedes: str, parameters: Mapping[str, float]) -> None:
        self.ref, self.supersedes, self.parameters = ref, supersedes, parameters


# --- the sequence in the pass ------------------------------------------------------------------


def test_the_pass_hands_every_escalation_to_the_chain(engine: Engine) -> None:
    a_failure_history(engine)
    outcome = CycleOutcome(
        market=MARKET,
        ref=REF,
        status=CycleStatus.NO_IMPROVEMENT,
        reason="deux comparaisons sans amélioration",
        comparisons=2,
    )
    cycle = RecordingCycle(outcome)
    notifier = FakeNotifier()

    run = asyncio.run(build_lab(engine, cycle=cycle, notifier=notifier).run_once(NOW))

    assert len(run.escalations) == 1
    assert len(cycle.calls) == 1
    escalation, at, incumbent = cycle.calls[0]
    assert escalation.market == MARKET
    assert at == NOW
    assert incumbent is None, "the chain reads its own baseline from the evidence"
    assert run.cycles == (outcome,)


def test_the_pass_says_where_the_chain_stopped(engine: Engine) -> None:
    a_failure_history(engine)
    notifier = FakeNotifier()
    outcome = CycleOutcome(
        market=MARKET,
        status=CycleStatus.SKIPPED_NO_EVIDENCE,
        reason="aucune preuve mesurée pour frxXAUUSD",
    )

    asyncio.run(build_lab(engine, cycle=RecordingCycle(outcome), notifier=notifier).run_once(NOW))

    assert any("aucune preuve" in text for text in notifier.sent)


def test_a_pass_without_a_chain_behaves_as_before(engine: Engine) -> None:
    a_failure_history(engine)

    run = asyncio.run(build_lab(engine).run_once(NOW))

    assert len(run.escalations) == 1
    assert run.cycles == ()


def test_a_pass_without_chain_or_escalation_does_not_reach_the_chain(engine: Engine) -> None:
    cycle = RecordingCycle(
        CycleOutcome(market=MARKET, status=CycleStatus.NO_VARIANTS, reason="rien à mesurer")
    )

    run = asyncio.run(build_lab(engine, cycle=cycle).run_once(NOW))

    assert run.escalations == ()
    assert cycle.calls == []


# --- the real chain, from the AI's proposal to the candidate ------------------------------------


def test_the_daily_pass_turns_a_proposal_into_a_measured_candidate(
    engine: Engine, tmp_path: Path
) -> None:
    a_failure_history(engine)
    record_incumbent(engine, objective=1000.0)
    with engine.begin() as connection:
        from tradingagent.storage.models import StrategyVersionRow

        connection.execute(
            insert(StrategyVersionRow).values(
                ref=REF,
                strategy_id="witness",
                version="1.1.0",
                manifest={"parameters": dict(PARAMETERS)},
                content_hash="c" * 64,
                first_seen_at=NOW - timedelta(days=60),
            )
        )
    store = LabStore(engine)
    store.record_proposal(
        ProposalRecord(
            market=MARKET,
            hypothesis="monter take_profit_rr à 2.5",
            proposed_change={
                "parameter": "take_profit_rr",
                "current_value": 2.0,
                "proposed_value": 2.5,
                "action": "increase",
            },
            created_at=NOW,
            ref=REF,
        )
    )
    written: list[Any] = []
    cycle = a_real_cycle(
        engine,
        objectives={"take_profit_rr=2.5": 1300.0},
        accept="take_profit_rr=2.5",
        written=written,
        target=tmp_path,
    )

    run = asyncio.run(build_lab(engine, cycle=cycle).run_once(NOW))

    assert len(run.cycles) == 1
    assert run.cycles[0].status is CycleStatus.IMPROVED
    assert written and written[0].parameters["take_profit_rr"] == 2.5
    entries = [row for row in _journal(engine) if row.kind == CYCLE_EVENT]
    assert entries[-1].detail["comparisons"] == 1


def test_without_any_measurement_the_pass_stops_cleanly_and_says_so(engine: Engine) -> None:
    """Today's case: the chain runs, finds no proof, and the operator is told."""
    a_failure_history(engine)
    notifier = FakeNotifier()
    written: list[Any] = []
    cycle = a_real_cycle(engine, objectives={}, accept=None, written=written)

    run = asyncio.run(build_lab(engine, cycle=cycle, notifier=notifier).run_once(NOW))

    assert run.cycles[0].status is CycleStatus.SKIPPED_NO_EVIDENCE
    assert written == []
    assert not any("retenue" in text for text in notifier.sent)
    assert any("aucune preuve" in text for text in notifier.sent)


def _journal(engine: Engine) -> list[SystemEventRow]:
    with Session(engine) as session:
        return list(session.scalars(select(SystemEventRow).order_by(SystemEventRow.id)).all())


# --- the bridge, seen from the consumer ----------------------------------------------------------


def test_the_daily_lab_finds_the_campaign_measurement(engine: Engine) -> None:
    """The proof that the missing bridge is closed: a recorded run is readable as evidence."""
    record_incumbent(engine, objective=1234.0)
    record_gate(
        engine,
        ref=REF,
        market=MARKET,
        stage=ValidationStage.PARAMETER_ROBUSTNESS,
        passed=False,
        detail={"most_sensitive": "atr_stop_multiple"},
        at=NOW - timedelta(hours=1),
    )

    evidence = build_lab(engine)._evidence(MARKET)

    assert isinstance(evidence, BacktestEvidence)
    assert evidence.ref == REF
    assert evidence.market == MARKET
    assert evidence.dataset_id == "xau-m15-2026"
    assert evidence.metrics["max_drawdown_eur"] == 90.0
    assert evidence.metrics["objective"] == 1234.0
    assert evidence.validations[0].stage == ValidationStage.PARAMETER_ROBUSTNESS.value
    assert evidence.validations[0].passed is False


def test_the_daily_lab_finds_no_evidence_when_nothing_was_measured(engine: Engine) -> None:
    assert build_lab(engine)._evidence(MARKET) is None


def test_a_recorded_campaign_run_makes_the_researcher_propose(engine: Engine) -> None:
    """The operator's complaint, closed end to end.

    A campaign records what it measured; the daily pass reads it back as evidence; the
    researcher turns it into a falsifiable hypothesis. Before the bridge, the last step was
    unreachable: `backtest_runs` stayed empty and the researcher proposed nothing, ever.
    """
    with engine.begin() as connection:
        connection.execute(
            insert(StrategyVersionRow).values(
                ref=REF,
                strategy_id="witness",
                version="1.1.0",
                manifest={
                    "parameters": {"risk_per_trade_pct": 1.0, "ema_fast": 20},
                    "strategy_id": "witness",
                    "version": "1.1.0",
                },
                content_hash="d" * 64,
                first_seen_at=NOW - timedelta(days=60),
            )
        )
    record_run(
        engine,
        MeasuredRun(
            market=MARKET,
            ref=REF,
            dataset_id="xau-m15-2026",
            fingerprint="a" * 64,
            window_start=NOW - timedelta(days=30),
            window_end=NOW,
            objective=-180.0,
            metrics={"max_drawdown_eur": 420.0, "trades": 120.0},
            costs={"spread": 0.3},
            comparisons=3,
        ),
        at=NOW - timedelta(days=1),
    )
    store = LabStore(engine)
    lab = DailyLab(
        engine,
        analyst=TradeAnalyst(store),
        researcher=StrategyResearcher(store),
        cycle=None,
        now=lambda: NOW,
    )

    proposals = asyncio.run(lab._research((a_closed_trade_fact(),), NOW))

    assert len(proposals) == 1
    change = proposals[0].proposed_change
    assert change["parameter"] == "risk_per_trade_pct"
    assert change["proposed_value"] == 0.5
    assert any("max_drawdown_eur=420" in citation for citation in change["evidence"])
    with Session(engine) as session:
        rows = list(session.scalars(select(AiProposalRow)).all())
    assert len(rows) == 1
    assert rows[0].ref == REF
    assert rows[0].status is ProposalStatus.PROPOSED


def a_closed_trade_fact() -> ClosedTradeFact:
    return ClosedTradeFact(
        trade=Trade(
            symbol=MARKET,
            strategy_ref=REF,
            direction=Direction.BUY,
            timeframe=Timeframe.M15,
            mode=TradingMode.SIGNAL,
            opened_at=NOW - timedelta(hours=4),
            closed_at=NOW - timedelta(hours=1),
            pnl_eur=Decimal("-12.5"),
            risk_eur=Decimal("5"),
            slippage=None,
            spread=None,
        ),
        signal_id=None,
        atr=None,
    )
