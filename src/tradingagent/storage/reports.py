"""Report persistence (F-022, TASK-042): what was generated, and whether it was sent.

The unique (period, window_start) key makes regeneration after a crash idempotent: the
second write loses, the first report is the one that gets sent.

A report carries the market it is about, when it is about exactly one — the rule lives in
`ReportGenerator.sole_market`, and a report covering several markets stores NULL. The
column exists so the operator's `/reports` page can be filtered without lying about what
the figures cover.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tradingagent.storage._conflicts import insert_ignoring_duplicates
from tradingagent.storage.models import ReportRow

UNIQUE_KEY = ("period", "window_start")


@dataclass(frozen=True)
class StoredReport:
    id: int
    period: str
    window_start: datetime
    window_end: datetime
    content: str
    sent_at: datetime | None
    market: str | None


class ReportStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save(
        self,
        period: str,
        window_start: datetime,
        window_end: datetime,
        content: str,
        at: datetime,
        market: str | None = None,
    ) -> None:
        """One report, with the market it is about when the caller knows one.

        `market` is the single market the window covered, or None for a report spanning
        several: the column is nullable, and an empty cell is an honest answer.
        """
        statement = insert_ignoring_duplicates(self._engine, ReportRow, UNIQUE_KEY).values(
            period=period,
            window_start=window_start,
            window_end=window_end,
            content=content,
            generated_at=at,
            market=market,
        )
        with self._engine.begin() as connection:
            connection.execute(statement)

    def unsent_before(self, at: datetime) -> list[StoredReport]:
        """Fully-elapsed windows without a sent mark, oldest first."""
        statement = (
            select(ReportRow)
            .where(ReportRow.sent_at.is_(None), ReportRow.window_end <= at)
            .order_by(ReportRow.window_start)
        )
        with Session(self._engine) as session:
            rows = session.scalars(statement).all()
        return [_stored(row) for row in rows]

    def find(self, period: str, window_start: datetime) -> StoredReport | None:
        statement = select(ReportRow).where(
            ReportRow.period == period, ReportRow.window_start == window_start
        )
        with Session(self._engine) as session:
            row = session.scalars(statement).first()
        return _stored(row) if row else None

    def mark_sent(self, report_id: int, at: datetime) -> None:
        with Session(self._engine) as session:
            row = session.get(ReportRow, report_id)
            if row is not None and row.sent_at is None:
                row.sent_at = at
                session.commit()

    def latest_window_end(self, period: str) -> datetime | None:
        statement = (
            select(ReportRow.window_end)
            .where(ReportRow.period == period)
            .order_by(ReportRow.window_end.desc())
            .limit(1)
        )
        with Session(self._engine) as session:
            return session.scalar(statement)


def _stored(row: ReportRow) -> StoredReport:
    return StoredReport(
        id=row.id,
        period=row.period,
        window_start=row.window_start,
        window_end=row.window_end,
        content=row.content,
        sent_at=row.sent_at,
        market=row.market,
    )
