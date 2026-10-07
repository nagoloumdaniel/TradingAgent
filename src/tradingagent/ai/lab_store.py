"""Persistence of what the AI Lab observed and proposed (cahier v3 Â§5, Â§39).

This module only ever writes to `ai_analyses` and `ai_proposals`. It cannot reach
`signals`, `orders`, `positions` or `trades`: an analysis is a note, a proposal is a
question, and neither can move capital. `record_proposal` stores PROPOSED by
construction, so no AI path can create a promoted row; promotion is a decision of the
validation system, recorded by `decide_proposal` with its actor, its reason and its UTC
moment.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.orm import Session

from tradingagent.core.states import AnalysisKind, ProposalStatus
from tradingagent.storage.models import AiAnalysisRow, AiProposalRow


@dataclass(frozen=True)
class AnalysisRecord:
    """One observation to persist: the verdict and its supporting figures."""

    kind: AnalysisKind
    market: str
    model: str
    request: dict[str, Any]
    findings: dict[str, Any]
    created_at: datetime
    ref: str | None = None
    signal_id: int | None = None
    response: str | None = None
    cost_eur: Decimal | None = None


@dataclass(frozen=True)
class ProposalRecord:
    """One falsifiable hypothesis to persist. Its status is always PROPOSED."""

    market: str
    hypothesis: str
    proposed_change: dict[str, Any]
    created_at: datetime
    ref: str | None = None
    analysis_id: int | None = None


@dataclass(frozen=True)
class StoredAnalysis:
    id: int
    kind: AnalysisKind
    market: str
    ref: str | None
    signal_id: int | None
    model: str
    request: dict[str, Any]
    response: str | None
    findings: dict[str, Any]
    cost_eur: Decimal | None
    created_at: datetime


@dataclass(frozen=True)
class StoredProposal:
    id: int
    market: str
    ref: str | None
    analysis_id: int | None
    hypothesis: str
    proposed_change: dict[str, Any]
    status: ProposalStatus
    created_at: datetime
    decided_at: datetime | None
    decided_by: str | None
    decision_reason: str | None


# Statuses a proposal may still receive a decision in. A decided proposal is history.
OPEN_STATUSES = (ProposalStatus.PROPOSED, ProposalStatus.VALIDATING)


def _require_utc(moment: datetime, name: str) -> None:
    if moment.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be UTC")


def _analysis(row: AiAnalysisRow) -> StoredAnalysis:
    return StoredAnalysis(
        id=row.id,
        kind=row.kind,
        market=row.market,
        ref=row.ref,
        signal_id=row.signal_id,
        model=row.model,
        request=dict(row.request),
        response=row.response,
        findings=dict(row.findings),
        cost_eur=row.cost_eur,
        created_at=row.created_at,
    )


def _proposal(row: AiProposalRow) -> StoredProposal:
    return StoredProposal(
        id=row.id,
        market=row.market,
        ref=row.ref,
        analysis_id=row.analysis_id,
        hypothesis=row.hypothesis,
        proposed_change=dict(row.proposed_change),
        status=row.status,
        created_at=row.created_at,
        decided_at=row.decided_at,
        decided_by=row.decided_by,
        decision_reason=row.decision_reason,
    )


class LabStore:
    """The only writer of the AI Lab tables. No network, no clock, no trading table."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record_analysis(self, analysis: AnalysisRecord) -> int:
        _require_utc(analysis.created_at, "created_at")
        with self._engine.begin() as connection:
            result = connection.execute(
                insert(AiAnalysisRow).values(
                    kind=analysis.kind,
                    market=analysis.market,
                    ref=analysis.ref,
                    signal_id=analysis.signal_id,
                    model=analysis.model,
                    request=analysis.request,
                    response=analysis.response,
                    findings=analysis.findings,
                    cost_eur=analysis.cost_eur,
                    created_at=analysis.created_at,
                )
            )
            primary_key = result.inserted_primary_key
            if primary_key is None:
                raise RuntimeError("insert returned no primary key")
            return int(primary_key[0])

    def record_proposal(self, proposal: ProposalRecord) -> int:
        """Insert a proposal. The status is PROPOSED, whatever the caller would prefer."""
        _require_utc(proposal.created_at, "created_at")
        if not proposal.hypothesis.strip():
            raise ValueError("a proposal needs a hypothesis")
        with self._engine.begin() as connection:
            result = connection.execute(
                insert(AiProposalRow).values(
                    market=proposal.market,
                    ref=proposal.ref,
                    analysis_id=proposal.analysis_id,
                    hypothesis=proposal.hypothesis,
                    proposed_change=proposal.proposed_change,
                    status=ProposalStatus.PROPOSED,
                    created_at=proposal.created_at,
                )
            )
            primary_key = result.inserted_primary_key
            if primary_key is None:
                raise RuntimeError("insert returned no primary key")
            return int(primary_key[0])

    def decide_proposal(
        self,
        proposal_id: int,
        status: ProposalStatus,
        actor: str,
        reason: str,
        at: datetime,
    ) -> None:
        """The validation system's move. Only an open proposal may be decided, once."""
        if status is ProposalStatus.PROPOSED:
            raise ValueError("a proposal cannot be set back to proposed")
        _require_utc(at, "at")
        actor, reason = actor.strip(), reason.strip()
        if not actor or not reason:
            raise ValueError("a decision requires an actor and a reason")
        with self._engine.begin() as connection:
            row = connection.execute(
                select(AiProposalRow.status).where(AiProposalRow.id == proposal_id)
            ).first()
            if row is None:
                raise ValueError(f"no proposal {proposal_id}")
            current = ProposalStatus(row[0])
            if current not in OPEN_STATUSES:
                raise ValueError(f"proposal {proposal_id} is already decided ({current.value})")
            connection.execute(
                update(AiProposalRow)
                .where(AiProposalRow.id == proposal_id)
                .values(
                    status=status,
                    decided_by=actor,
                    decision_reason=reason,
                    decided_at=at,
                )
            )

    def recent_analyses(
        self,
        *,
        kind: AnalysisKind | None = None,
        market: str | None = None,
        limit: int = 50,
    ) -> tuple[StoredAnalysis, ...]:
        if limit <= 0:
            return ()
        statement = select(AiAnalysisRow).order_by(
            AiAnalysisRow.created_at.desc(), AiAnalysisRow.id.desc()
        )
        if kind is not None:
            statement = statement.where(AiAnalysisRow.kind == kind)
        if market is not None:
            statement = statement.where(AiAnalysisRow.market == market)
        with Session(self._engine) as session:
            rows = session.scalars(statement.limit(limit)).all()
            return tuple(_analysis(row) for row in rows)

    def open_proposals(self) -> tuple[StoredProposal, ...]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(AiProposalRow)
                .where(AiProposalRow.status.in_(OPEN_STATUSES))
                .order_by(AiProposalRow.created_at, AiProposalRow.id)
            ).all()
            return tuple(_proposal(row) for row in rows)

    def recent_proposals(
        self, *, market: str | None = None, limit: int = 50
    ) -> tuple[StoredProposal, ...]:
        """The latest proposals, decided or not, newest first (the operator's view).

        Read-only, like every read of this store: the operator sees what was proposed and
        where the decision stands. A decision is never recomputed here, only displayed.
        """
        if limit <= 0:
            return ()
        statement = select(AiProposalRow).order_by(
            AiProposalRow.created_at.desc(), AiProposalRow.id.desc()
        )
        if market is not None:
            statement = statement.where(AiProposalRow.market == market)
        with Session(self._engine) as session:
            rows = session.scalars(statement.limit(limit)).all()
            return tuple(_proposal(row) for row in rows)
