"""System events: the observability ledger every component writes to (section 11).

`system_events` is the append-only record of warnings, refusals and state changes that
do not belong to a specific signal. The operator's /mode command records here (TASK-023),
and the health alerting reads from it (TASK-024).
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, desc, insert, select
from sqlalchemy.orm import Session

from tradingagent.core.states import Severity
from tradingagent.storage.models import SystemEventRow


class SystemEventStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(
        self, kind: str, severity: Severity, detail: Mapping[str, Any], at: datetime
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(SystemEventRow).values(
                    kind=kind, severity=severity, detail=dict(detail), occurred_at=at
                )
            )

    def latest(self, kind: str) -> SystemEventRow | None:
        statement = (
            select(SystemEventRow)
            .where(SystemEventRow.kind == kind)
            .order_by(desc(SystemEventRow.occurred_at))
            .limit(1)
        )
        with Session(self._engine) as session:
            return session.scalars(statement).first()
