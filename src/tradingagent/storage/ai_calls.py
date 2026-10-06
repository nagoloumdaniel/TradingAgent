"""AI call persistence (F-010, EF-030, TASK-037).

Every request and response is kept in full for the audit (F-020), and the cumulated cost
is readable from the same table: the budget check and the operator's reports share it.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Engine, func, insert, select
from sqlalchemy.orm import Session

from tradingagent.core.mode import AiFilter
from tradingagent.storage.models import AiCallRow


@dataclass(frozen=True)
class AiReply:
    """What a model returned, before any interpretation."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class AiCall:
    signal_id: int | None
    purpose: str
    ai_filter: AiFilter
    request: dict[str, object]
    response: str | None
    verdict: str | None
    error: str | None
    latency_ms: int | None
    cost_eur: Decimal | None
    called_at: datetime
    model: str = "unknown"


class AiCallStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(self, call: AiCall) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(AiCallRow).values(
                    signal_id=call.signal_id,
                    purpose=call.purpose,
                    model=call.model,
                    ai_filter=call.ai_filter,
                    request=call.request,
                    response=call.response,
                    verdict=call.verdict,
                    error=call.error,
                    latency_ms=call.latency_ms,
                    cost_eur=call.cost_eur,
                    called_at=call.called_at,
                )
            )

    def total_cost_eur(self) -> Decimal:
        statement = select(func.coalesce(func.sum(AiCallRow.cost_eur), 0))
        with Session(self._engine) as session:
            return Decimal(str(session.scalar(statement)))

    def verdicts_between(
        self, start: datetime, end: datetime, purpose: str
    ) -> tuple[set[int], int, Decimal]:
        """The AI-rejected signal ids, how many signals were evaluated, and the cost —
        the raw material of the shadow-filter evaluation (TASK-044)."""
        rejected: set[int] = set()
        evaluated: set[int] = set()
        cost = Decimal(0)
        with Session(self._engine) as session:
            calls = session.scalars(
                select(AiCallRow).where(
                    AiCallRow.called_at >= start,
                    AiCallRow.called_at < end,
                    AiCallRow.purpose == purpose,
                )
            ).all()
        for call in calls:
            if call.signal_id is not None:
                evaluated.add(call.signal_id)
                if call.verdict == "rejected":
                    rejected.add(call.signal_id)
            if call.cost_eur is not None:
                cost += call.cost_eur
        return rejected, len(evaluated), cost
