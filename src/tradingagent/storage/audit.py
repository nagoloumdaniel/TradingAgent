"""Operator commands and decisions, append-only (F-014: the history is consultable)."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, insert, select
from sqlalchemy.orm import Session

from tradingagent.storage.models import AuditLogRow


class AuditStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(self, actor: str, action: str, detail: Mapping[str, Any], at: datetime) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(AuditLogRow).values(
                    actor=actor, action=action, detail=dict(detail), occurred_at=at
                )
            )

    def recent(self, limit: int = 50) -> list[AuditLogRow]:
        with Session(self._engine) as session:
            return list(
                session.scalars(
                    select(AuditLogRow).order_by(AuditLogRow.id.desc()).limit(limit)
                ).all()
            )
