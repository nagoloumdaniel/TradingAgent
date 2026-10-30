"""The news repository: the calendar, the readings, the daily brief and the feed log.

Plain upserts and reads. Nothing here decides anything: the gate and the dashboard read what the
refresh wrote. The cost of the model calls is read back from `ai_calls`, the table every model
call is journalled in, so the budget cannot drift from what was really spent.
"""

from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, delete, func, insert, select
from sqlalchemy.orm import Session

from tradingagent.news.model import (
    Bias,
    DailyBrief,
    EconomicEvent,
    EventAnalysis,
    EventKind,
    Impact,
    MarketBrief,
    MarketVerdict,
    Stance,
    sanitize_event,
)
from tradingagent.news.ports import SourceReport
from tradingagent.storage.models import (
    AiCallRow,
    EconomicEventRow,
    NewsAnalysisRow,
    NewsBriefRow,
    NewsFetchRow,
)

#: The `ai_calls.purpose` values the news package writes (the column holds 32 characters).
PURPOSE_PREFIX = "news_"


def _event(row: EconomicEventRow) -> EconomicEvent:
    return EconomicEvent(
        key=row.key,
        starts_at=row.starts_at,
        currency=row.currency,
        title=row.title,
        impact=Impact(row.impact),
        kind=EventKind(row.kind),
        country=row.country,
        forecast=row.forecast,
        previous=row.previous,
        actual=row.actual,
        source=row.source,
        url=row.url,
        summary=row.summary,
    )


def _verdict_json(verdict: MarketVerdict) -> dict[str, Any]:
    return {
        "symbol": verdict.symbol,
        "bias": verdict.bias.value,
        "confidence": verdict.confidence,
        "influence": verdict.influence,
        "stance": verdict.stance.value,
        "timeframes": list(verdict.timeframes),
        "window_start": verdict.window_start.isoformat(),
        "window_end": verdict.window_end.isoformat(),
        "rationale": verdict.rationale,
    }


def _verdict(data: Mapping[str, Any]) -> MarketVerdict:
    return MarketVerdict(
        symbol=str(data["symbol"]),
        bias=Bias(data["bias"]),
        confidence=float(data["confidence"]),
        influence=int(data["influence"]),
        stance=Stance(data["stance"]),
        timeframes=tuple(str(t) for t in data["timeframes"]),
        window_start=datetime.fromisoformat(data["window_start"]),
        window_end=datetime.fromisoformat(data["window_end"]),
        rationale=str(data["rationale"]),
    )


class NewsStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # --- events ----------------------------------------------------------------------------

    def upsert_events(self, events: Iterable[EconomicEvent], now: datetime) -> int:
        """Insert new announcements, update the ones whose figures moved. Returns the changes."""
        changed = 0
        with Session(self._engine) as session, session.begin():
            for raw in events:
                event = sanitize_event(raw)
                if event is None:  # not a storable announcement: skipped, the batch goes on
                    continue
                row = session.scalar(
                    select(EconomicEventRow).where(EconomicEventRow.key == event.key)
                )
                values = {
                    "source": event.source[:32],
                    "starts_at": event.starts_at,
                    "currency": event.currency,
                    "title": event.title,
                    "impact": event.impact.value,
                    "kind": event.kind.value,
                    "country": event.country,
                    "forecast": event.forecast,
                    "previous": event.previous,
                    "actual": event.actual,
                    "url": event.url,
                    "summary": event.summary,
                }
                if row is None:
                    session.add(
                        EconomicEventRow(key=event.key, first_seen_at=now, updated_at=now, **values)
                    )
                    changed += 1
                    continue
                if any(getattr(row, name) != value for name, value in values.items()):
                    for name, value in values.items():
                        setattr(row, name, value)
                    row.updated_at = now
                    changed += 1
        return changed

    def withdraw(
        self, source: str, start: datetime, end: datetime, keep_keys: Iterable[str]
    ) -> int:
        """Remove what `source` stopped listing inside the span it is authoritative for."""
        keep = set(keep_keys)
        statement = select(EconomicEventRow).where(
            EconomicEventRow.source == source,
            EconomicEventRow.starts_at >= start,
            EconomicEventRow.starts_at <= end,
        )
        removed = 0
        with Session(self._engine) as session, session.begin():
            for row in session.scalars(statement).all():
                if row.key not in keep:
                    session.delete(row)
                    removed += 1
        return removed

    def events_between(self, start: datetime, end: datetime) -> list[EconomicEvent]:
        """Events with `start <= starts_at < end`, in time order."""
        statement = (
            select(EconomicEventRow)
            .where(EconomicEventRow.starts_at >= start, EconomicEventRow.starts_at < end)
            .order_by(EconomicEventRow.starts_at, EconomicEventRow.id)
        )
        with Session(self._engine) as session:
            return [_event(row) for row in session.scalars(statement).all()]

    def count_events(self) -> int:
        with Session(self._engine) as session:
            return int(session.scalar(select(func.count(EconomicEventRow.id))) or 0)

    def purge_before(self, cutoff: datetime) -> int:
        """Forget what is older than `cutoff`: old events, their readings, old feed logs."""
        with self._engine.begin() as connection:
            removed = connection.execute(
                delete(EconomicEventRow).where(EconomicEventRow.starts_at < cutoff)
            ).rowcount
            connection.execute(delete(NewsAnalysisRow).where(NewsAnalysisRow.analysed_at < cutoff))
            connection.execute(delete(NewsFetchRow).where(NewsFetchRow.fetched_at < cutoff))
        return int(removed)

    # --- analyses --------------------------------------------------------------------------

    def save_analysis(self, analysis: EventAnalysis) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(NewsAnalysisRow).values(
                    event_key=analysis.event_key,
                    input_hash=analysis.input_hash,
                    source=analysis.source,
                    model=analysis.model[:64],
                    verdicts=[_verdict_json(v) for v in analysis.verdicts],
                    analysed_at=analysis.analysed_at,
                )
            )

    def latest_analyses(self, keys: Iterable[str]) -> dict[str, EventAnalysis]:
        """The most recent reading of each event (the latest row wins)."""
        wanted = list(dict.fromkeys(keys))
        if not wanted:
            return {}
        found: dict[str, EventAnalysis] = {}
        with Session(self._engine) as session:
            # Chunked: a week of calendar is ~150 keys, far below any bound-parameter limit, but
            # the guard costs nothing.
            for offset in range(0, len(wanted), 400):
                chunk = wanted[offset : offset + 400]
                latest = (
                    select(func.max(NewsAnalysisRow.id))
                    .where(NewsAnalysisRow.event_key.in_(chunk))
                    .group_by(NewsAnalysisRow.event_key)
                )
                rows = session.scalars(
                    select(NewsAnalysisRow).where(NewsAnalysisRow.id.in_(latest))
                ).all()
                for row in rows:
                    found[row.event_key] = EventAnalysis(
                        event_key=row.event_key,
                        verdicts=tuple(_verdict(v) for v in row.verdicts),
                        source=row.source,
                        model=row.model,
                        analysed_at=row.analysed_at,
                        input_hash=row.input_hash,
                    )
        return found

    # --- briefs ----------------------------------------------------------------------------

    def save_brief(self, brief: DailyBrief) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(NewsBriefRow).values(
                    day=brief.day,
                    source=brief.source,
                    model=brief.model[:64],
                    headline=brief.headline,
                    markets=[
                        {
                            "symbol": m.symbol,
                            "bias": m.bias.value,
                            "stance": m.stance.value,
                            "summary": m.summary,
                        }
                        for m in brief.markets
                    ],
                    generated_at=brief.generated_at,
                )
            )

    def latest_brief(self, day: str | None = None) -> DailyBrief | None:
        statement = select(NewsBriefRow).order_by(NewsBriefRow.id.desc()).limit(1)
        if day is not None:
            statement = statement.where(NewsBriefRow.day == day)
        with Session(self._engine) as session:
            row = session.scalar(statement)
            if row is None:
                return None
            return DailyBrief(
                day=row.day,
                headline=row.headline,
                markets=tuple(
                    MarketBrief(
                        symbol=str(m["symbol"]),
                        bias=Bias(m["bias"]),
                        stance=Stance(m["stance"]),
                        summary=str(m["summary"]),
                    )
                    for m in row.markets
                ),
                source=row.source,
                model=row.model,
                generated_at=row.generated_at,
            )

    # --- feed log --------------------------------------------------------------------------

    def record_fetch(self, report: SourceReport) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(NewsFetchRow).values(
                    source=report.source,
                    ok=report.ok,
                    items=report.items,
                    status=report.status,
                    error=report.error,
                    note=report.note,
                    retry_after=report.retry_after,
                    fetched_at=report.fetched_at,
                )
            )

    def latest_fetches(self) -> list[SourceReport]:
        """The last fetch of each source, by source name."""
        latest = select(func.max(NewsFetchRow.id)).group_by(NewsFetchRow.source)
        with Session(self._engine) as session:
            rows = session.scalars(
                select(NewsFetchRow)
                .where(NewsFetchRow.id.in_(latest))
                .order_by(NewsFetchRow.source)
            ).all()
            return [
                SourceReport(
                    source=row.source,
                    ok=row.ok,
                    items=row.items,
                    fetched_at=row.fetched_at,
                    status=row.status,
                    error=row.error,
                    note=row.note,
                    retry_after=row.retry_after,
                )
                for row in rows
            ]

    def last_success(self, source: str) -> datetime | None:
        with Session(self._engine) as session:
            return session.scalar(
                select(func.max(NewsFetchRow.fetched_at)).where(
                    NewsFetchRow.source == source, NewsFetchRow.ok.is_(True)
                )
            )

    # --- model cost ------------------------------------------------------------------------

    def ai_cost_between(self, start: datetime, end: datetime) -> Decimal:
        """What the news model calls cost in [start, end): summed in Python (exact decimals)."""
        statement = select(AiCallRow.cost_eur).where(
            AiCallRow.purpose.like(f"{PURPOSE_PREFIX}%"),
            AiCallRow.called_at >= start,
            AiCallRow.called_at < end,
        )
        with Session(self._engine) as session:
            return sum(
                (cost for cost in session.scalars(statement).all() if cost is not None),
                Decimal(0),
            )

    def ai_calls_between(self, start: datetime, end: datetime) -> int:
        statement = select(func.count(AiCallRow.id)).where(
            AiCallRow.purpose.like(f"{PURPOSE_PREFIX}%"),
            AiCallRow.called_at >= start,
            AiCallRow.called_at < end,
        )
        with Session(self._engine) as session:
            return int(session.scalar(statement) or 0)
