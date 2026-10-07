"""Execution telemetry (cahier v3 §20, §47).

Every hop from a signal to a fill is timestamped, so the dashboard can show real latencies,
slippage and rejection rates instead of guesses. The table is append-only: telemetry is
history, never rewritten.
"""

from dataclasses import dataclass
from datetime import datetime
from statistics import median

from sqlalchemy import Engine, insert, select
from sqlalchemy.orm import Session

from tradingagent.core.states import ExecutionEventKind
from tradingagent.storage.models import ExecutionEventRow


@dataclass(frozen=True)
class ExecutionEvent:
    id: int
    kind: ExecutionEventKind
    symbol: str
    detail: dict[str, object]
    occurred_at: datetime


@dataclass(frozen=True)
class LatencyStats:
    """Milliseconds between two consecutive hops of the same order or signal."""

    sample: int
    median_ms: float | None
    worst_ms: float | None


class ExecutionEventStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(
        self,
        kind: ExecutionEventKind,
        symbol: str,
        detail: dict[str, object],
        at: datetime,
        *,
        order_id: int | None = None,
        signal_id: int | None = None,
    ) -> int:
        with self._engine.begin() as connection:
            event_id = connection.execute(
                insert(ExecutionEventRow)
                .values(
                    order_id=order_id,
                    signal_id=signal_id,
                    symbol=symbol,
                    kind=kind,
                    detail=detail,
                    occurred_at=at,
                )
                .returning(ExecutionEventRow.id)
            ).scalar_one()
        return int(event_id)

    def recent(self, limit: int = 100, symbol: str | None = None) -> list[ExecutionEvent]:
        statement = select(ExecutionEventRow).order_by(ExecutionEventRow.occurred_at.desc())
        if symbol is not None:
            statement = statement.where(ExecutionEventRow.symbol == symbol)
        with Session(self._engine) as session:
            rows = session.scalars(statement.limit(limit)).all()
        return [
            ExecutionEvent(
                id=int(row.id),
                kind=row.kind,
                symbol=row.symbol,
                detail=dict(row.detail),
                occurred_at=row.occurred_at,
            )
            for row in rows
        ]

    def count_by_kind(self, symbol: str | None = None) -> dict[str, int]:
        statement = select(ExecutionEventRow.kind, ExecutionEventRow.detail)
        if symbol is not None:
            statement = statement.where(ExecutionEventRow.symbol == symbol)
        with Session(self._engine) as session:
            rows = session.execute(statement).all()
        counts: dict[str, int] = {}
        for kind, _ in rows:
            counts[str(kind)] = counts.get(str(kind), 0) + 1
        return counts

    def latency(
        self,
        start: ExecutionEventKind,
        end: ExecutionEventKind,
        *,
        symbol: str | None = None,
    ) -> LatencyStats:
        """Pair each `start` event with the first `end` event that follows it, per order."""
        samples: list[float] = []
        for events in self._grouped().values():
            if symbol is not None and events[0].symbol != symbol:
                continue
            ends = [event.occurred_at for event in events if event.kind is end]
            for event in events:
                if event.kind is not start:
                    continue
                after = [moment for moment in ends if moment >= event.occurred_at]
                if after:
                    samples.append((min(after) - event.occurred_at).total_seconds() * 1000)
        if not samples:
            return LatencyStats(0, None, None)
        return LatencyStats(len(samples), median(samples), max(samples))

    def _grouped(self) -> dict[object, list[ExecutionEvent]]:
        statement = select(ExecutionEventRow).order_by(ExecutionEventRow.occurred_at)
        with Session(self._engine) as session:
            rows = session.scalars(statement).all()
        grouped: dict[object, list[ExecutionEvent]] = {}
        for row in rows:
            event = ExecutionEvent(
                id=int(row.id),
                kind=row.kind,
                symbol=row.symbol,
                detail=dict(row.detail),
                occurred_at=row.occurred_at,
            )
            key = row.order_id if row.order_id is not None else row.signal_id
            grouped.setdefault(key, []).append(event)
        return grouped
