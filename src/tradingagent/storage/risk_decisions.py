"""Risk decisions, refusals included, with every check's verdict (TASK-035, action 3).

The decision row, the signal's new state and its lifecycle event are written together.
Refusals are counted per check for the daily report.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.orm import Session

from tradingagent.core.states import RiskOutcome, SignalState
from tradingagent.risk.engine import RiskDecision
from tradingagent.risk.explain import explain_refusal, refusal_causes
from tradingagent.storage.models import RiskDecisionRow, SignalEventRow, SignalRow


@dataclass(frozen=True)
class RefusalSummary:
    decisions: int
    refused: int
    by_check: dict[str, int] = field(default_factory=dict)


class RiskDecisionStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(self, signal_id: int, decision: RiskDecision, at: datetime) -> int:
        refused = decision.outcome is RiskOutcome.REFUSED
        state = SignalState.RISK_REJECTED if refused else SignalState.VALIDATED
        sizing = decision.sizing
        with self._engine.begin() as connection:
            decision_id = connection.execute(
                insert(RiskDecisionRow)
                .values(
                    signal_id=signal_id,
                    outcome=decision.outcome,
                    reason=decision.reason,
                    checks=decision.checks_record(),
                    volume=None if sizing is None else sizing.volume,
                    risk_eur=None if sizing is None else sizing.risk_eur,
                    margin_eur=None if sizing is None else sizing.margin_eur,
                    decided_at=at,
                )
                .returning(RiskDecisionRow.id)
            ).scalar_one()
            connection.execute(
                insert(SignalEventRow).values(
                    signal_id=signal_id, state=state, occurred_at=at, detail=decision.reason
                )
            )
            connection.execute(
                update(SignalRow).where(SignalRow.id == signal_id).values(state=state)
            )
        return int(decision_id)

    def refusals(self, start: datetime, end: datetime) -> RefusalSummary:
        """Decisions taken in [start, end), and how often each check refused."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(RiskDecisionRow.outcome, RiskDecisionRow.checks).where(
                    RiskDecisionRow.decided_at >= start, RiskDecisionRow.decided_at < end
                )
            ).all()
        by_check: Counter[str] = Counter()
        refused = 0
        for outcome, checks in rows:
            if outcome is not RiskOutcome.REFUSED:
                continue
            refused += 1
            # Root causes only: a check blocked by another failure is not a second cause.
            by_check.update(
                name
                for name, verdict in checks.items()
                if not verdict["passed"] and not verdict.get("blocked_by")
            )
        return RefusalSummary(len(rows), refused, dict(by_check))

    def refusal_digest(self, start: datetime, end: datetime, limit: int = 8) -> str:
        """The refusals of [start, end) by plain-French cause, most frequent first, with one
        explicit example each. Data for the AI Lab's prompts: "" when nothing was refused."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(RiskDecisionRow.reason)
                .where(
                    RiskDecisionRow.outcome == RiskOutcome.REFUSED,
                    RiskDecisionRow.decided_at >= start,
                    RiskDecisionRow.decided_at < end,
                )
                .order_by(RiskDecisionRow.decided_at.desc())
            ).all()
        counts: Counter[str] = Counter()
        example: dict[str, str] = {}
        for (reason,) in rows:
            full = explain_refusal(reason)
            for cause in refusal_causes(reason):
                counts[cause] += 1
                example.setdefault(cause, full)
        return "\n".join(
            f"- {cause} : {count} refus (ex. {example[cause]})"
            for cause, count in counts.most_common(limit)
        )


def latest_risk_eur(engine: Engine, signal_id: int) -> Decimal:
    """The risk the engine authorized for a signal, the denominator of its R multiple."""
    with Session(engine) as session:
        value = session.scalars(
            select(RiskDecisionRow.risk_eur)
            .where(RiskDecisionRow.signal_id == signal_id)
            .order_by(RiskDecisionRow.id.desc())
            .limit(1)
        ).first()
    return Decimal(0) if value is None else Decimal(value)
