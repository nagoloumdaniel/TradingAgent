"""The news service: refresh the calendar, read the new, write the morning brief, answer the gate.

One object the runtime loop ticks and the pipeline asks. It owns no clock (every method takes
`now`), never raises into its caller, and never lets a dead feed look like an empty calendar:

* a source that fails is logged (`news_fetches`), told to the operator once an hour as a system
  event, and left alone for as long as its host asked (HTTP 429 `Retry-After`); the events already
  stored stay where they were;
* the gate (`check`) is **fail-open** by default: when the calendar has not been refreshed for
  `max_staleness_minutes` (counted on the primary calendar only: a fresh optional next-week feed
  never hides a dead primary one), or the last export was an empty weekday, it lets signals
  through and says it was blind (`degraded`). With `news.fail_closed_when_blind` and in live
  mode only, it refuses instead (reject-only, with a motif): the operator chose to stay out of
  the market rather than trade without the calendar;
* the model only ever reads what is new or changed, within the daily budget; everything else is
  read by the rules, so the calendar is complete with or without a key.
"""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from tradingagent.core.states import Severity
from tradingagent.news.analyst import NewsAnalyst
from tradingagent.news.blackout import CLEAR, BlackoutDecision, news_blackout
from tradingagent.news.config import DEFAULT_NEWS_CONFIG, NewsConfig
from tradingagent.news.market_state import MarketStateReader
from tradingagent.news.model import (
    EconomicEvent,
    EventAnalysis,
    EventKind,
    Impact,
    MarketState,
    input_hash,
    is_major,
)
from tradingagent.news.ports import EMPTY_CALENDAR, EventSource, FetchResult, SourceReport
from tradingagent.resilience import one_line
from tradingagent.storage.events import SystemEventStore
from tradingagent.storage.news import NewsStore

log = logging.getLogger(__name__)

#: The primary scheduled calendar: the gate's freshness is this source and no other (the
#: `faireconomy_next` week is optional and answers 200 while the primary one may be down).
CALENDAR_SOURCE = "faireconomy"
#: A source that has not answered after this long is abandoned for this refresh (the HTTP layer
#: has its own wall-clock deadline; this one also covers a feed that hangs inside the parser).
SOURCE_DEADLINE_SECONDS = 60.0
#: A title in the model's context is cut here: a feed title is data, never a prompt.
SUMMARY_TITLE_CHARS = 140
#: Past events are only worth reading for a few hours after they happen.
ANALYSE_LOOKBACK = timedelta(hours=3)
#: A deterministic reading is looked at again by the model after this long, if it is available.
RETRY_FALLBACK_AFTER = timedelta(hours=2)
MAX_MODEL_CALLS_PER_REFRESH = 2
FAILURE_EVENT_EVERY = timedelta(hours=1)
KEEP_EVENTS_FOR = timedelta(days=21)
#: The widest blackout window is 240 minutes either side; the gate looks that far.
GATE_LOOKAROUND = timedelta(minutes=240)

KIND_REFRESH_FAILED = "news_refresh_failed"
KIND_SOURCE_FAILED = "news_source_failed"


@dataclass(frozen=True)
class RefreshReport:
    reports: tuple[SourceReport, ...] = ()
    changed: int = 0
    analysed: int = 0
    from_model: int = 0
    brief_written: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


class NewsService:
    def __init__(
        self,
        store: NewsStore,
        sources: Sequence[EventSource],
        analyst: NewsAnalyst,
        states: MarketStateReader,
        events: SystemEventStore | None = None,
        config: NewsConfig = DEFAULT_NEWS_CONFIG,
        *,
        live: bool = False,
    ) -> None:
        self._store = store
        self._sources = tuple(sources)
        self._analyst = analyst
        self._states = states
        self._events = events
        self._config = config
        self._live = live
        self._source_deadline = SOURCE_DEADLINE_SECONDS
        self._last_refresh: datetime | None = None
        self._blocked_until: dict[str, datetime] = {}
        self._failure_told: dict[str, datetime] = {}

    @property
    def config(self) -> NewsConfig:
        return self._config

    # --- the loop side ---------------------------------------------------------------------

    def due(self, now: datetime) -> bool:
        if not self._config.enabled:
            return False
        if self._last_refresh is None:
            return True
        return now - self._last_refresh >= timedelta(minutes=self._config.poll_minutes)

    async def tick(self, now: datetime) -> RefreshReport | None:
        """Refresh when it is time. Never raises: a news failure must not stop the loop."""
        if not self.due(now):
            return None
        self._last_refresh = now  # even on failure: no hammering a broken dependency
        try:
            return await self.refresh(now)
        except Exception as error:
            log.warning("news refresh failed: %s", one_line(error))
            await self._tell(KIND_REFRESH_FAILED, Severity.WARNING, {"error": one_line(error)}, now)
            return None

    async def refresh(self, now: datetime) -> RefreshReport:
        reports: list[SourceReport] = []
        changed = 0
        notes: list[str] = []
        due_sources = [s for s in self._sources if self._blocked_until.get(s.name, now) <= now]
        results = await asyncio.gather(
            *(self._fetch(source, now) for source in due_sources), return_exceptions=True
        )
        for source, result in zip(due_sources, results, strict=True):
            if isinstance(result, BaseException):
                report = SourceReport(source.name, False, 0, now, error=one_line(result))
                result = FetchResult((), report)
            report, moved = await self._ingest(source.name, result, now)
            reports.append(report)
            changed += moved
        for source in self._sources:
            if source not in due_sources:
                notes.append(f"{source.name}: en attente (limite de débit)")

        states = await self._states.read_async(now)
        analysed, from_model, note = await self._analyse_pending(states, now)
        if note:
            notes.append(note)
        brief_written = await self._daily_pass(states, now)
        return RefreshReport(
            tuple(reports), changed, analysed, from_model, brief_written, tuple(notes)
        )

    async def _fetch(self, source: EventSource, now: datetime) -> FetchResult:
        """One source, bounded: a hung feed costs this refresh its answer, never the loop."""
        try:
            return await asyncio.wait_for(source.fetch(now), timeout=self._source_deadline)
        except TimeoutError:
            error = f"délai dépassé ({self._source_deadline:.0f} s)"
            return FetchResult((), SourceReport(source.name, False, 0, now, error=error))

    async def _ingest(
        self, name: str, result: FetchResult, now: datetime
    ) -> tuple[SourceReport, int]:
        """Store what a source returned. The fetch is logged ok only once the write succeeded."""
        report = result.report
        changed = 0
        if report.ok:
            try:
                changed = await asyncio.to_thread(self._store.upsert_events, result.events, now)
                if result.covers is not None:
                    start, end = result.covers
                    await asyncio.to_thread(
                        self._store.withdraw, name, start, end, [e.key for e in result.events]
                    )
            except Exception as error:
                log.warning("news write failed for %s: %s", name, one_line(error))
                report = SourceReport(
                    name, False, 0, now, status=report.status, error=one_line(error)
                )
                changed = 0
            else:
                self._blocked_until.pop(name, None)
        await asyncio.to_thread(self._store.record_fetch, report)
        if not report.ok:
            if report.retry_after:
                self._blocked_until[name] = now + timedelta(seconds=report.retry_after)
            await self._tell_source_failure(report, now)
        return report, changed

    async def _analyse_pending(
        self, states: Mapping[str, MarketState], now: datetime
    ) -> tuple[int, int, str]:
        horizon = now + timedelta(days=self._config.horizon_days + 1)
        events = await asyncio.to_thread(
            self._store.events_between, now - ANALYSE_LOOKBACK, horizon
        )
        known = await asyncio.to_thread(self._store.latest_analyses, [e.key for e in events])
        pending = [e for e in events if _needs_reading(e, known.get(e.key), now)]
        if not pending:
            return 0, 0, ""
        analysed = from_model = processed = 0
        note = ""
        chunk = self._config.ai_max_events_per_call
        for _ in range(MAX_MODEL_CALLS_PER_REFRESH):
            batch = pending[processed : processed + chunk]
            if not batch:
                break
            run = await self._analyst.analyse(batch, states, now)
            for analysis in run.analyses:
                await asyncio.to_thread(self._store.save_analysis, analysis)
            processed += len(batch)
            analysed += len(run.analyses)
            from_model += run.from_model
            note = run.note or note
            if run.asked == 0:  # the model is not available: the rules read the rest below
                break
        for analysis in self._analyst.read_by_rules(pending[processed:], states, now):
            await asyncio.to_thread(self._store.save_analysis, analysis)
            analysed += 1
        return analysed, from_model, note

    async def _daily_pass(self, states: Mapping[str, MarketState], now: datetime) -> bool:
        """After the configured hour, once per UTC day: the morning brief, and a purge."""
        if now.hour < self._config.daily_hour_utc:
            return False
        day = now.strftime("%Y-%m-%d")
        if await asyncio.to_thread(self._store.latest_brief, day) is not None:
            return False
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today = await asyncio.to_thread(
            self._store.events_between, start, start + timedelta(days=1)
        )
        analyses = await asyncio.to_thread(self._store.latest_analyses, [e.key for e in today])
        brief = await self._analyst.brief(day, today, analyses, states, now)
        await asyncio.to_thread(self._store.save_brief, brief)
        await asyncio.to_thread(self._store.purge_before, now - KEEP_EVENTS_FOR)
        return True

    # --- the gate --------------------------------------------------------------------------

    def staleness(self, now: datetime) -> timedelta | None:
        """Time since the primary calendar was last fetched successfully, None if never.

        Only the primary source counts: `faireconomy_next` answers 200 or 404 on its own and
        would keep the gate looking fresh while the real calendar has been dead for hours.
        """
        seen = self._store.last_success(CALENDAR_SOURCE)
        return None if seen is None else now - seen

    def blindness(self, now: datetime) -> str:
        """Why the gate cannot see the calendar right now, or an empty string when it can."""
        age = self.staleness(now)
        if age is None:
            return "calendrier jamais rafraîchi"
        if age > timedelta(minutes=self._config.max_staleness_minutes):
            return f"calendrier rafraîchi il y a {int(age.total_seconds() // 60)} min"
        for report in self._store.latest_fetches():
            if report.source == CALENDAR_SOURCE and (report.error or "").startswith(EMPTY_CALENDAR):
                return f"{EMPTY_CALENDAR} : le dernier export n'annonçait rien un jour de semaine"
        return ""

    def check(self, symbol: str, now: datetime) -> BlackoutDecision:
        """May a new signal on `symbol` be taken now? Fail-open, and honest about being blind."""
        config = self._config
        if not config.enabled or not config.blackout.enabled:
            return CLEAR
        blind = self.blindness(now)
        if blind:
            if config.fail_closed_when_blind and self._live:
                reason = f"{blind} : signal refusé (news.fail_closed_when_blind actif en mode réel)"
                return BlackoutDecision(blocked=True, reason=reason, degraded=blind)
            return BlackoutDecision(blocked=False, degraded=blind)
        events = self._store.events_between(now - GATE_LOOKAROUND, now + GATE_LOOKAROUND)
        return news_blackout(symbol, now, events, config.blackout)

    def upcoming_summary(self, now: datetime, hours: int = 24, limit: int = 8) -> str:
        """A compact list of what is about to land, for the model's context. Empty if nothing."""
        if not self._config.enabled:
            return ""
        events = self._store.events_between(now - timedelta(hours=1), now + timedelta(hours=hours))
        picked = [e for e in events if _worth_telling(e)][:limit]
        stale = bool(self.blindness(now))
        if not picked and not stale:
            return ""
        lines = [_summary_line(e, now) for e in picked]
        if stale:
            lines.append("(calendrier non rafraîchi récemment : liste possiblement incomplète)")
        return "\n".join(lines)

    # --- operator messages -----------------------------------------------------------------

    async def _tell_source_failure(self, report: SourceReport, now: datetime) -> None:
        last = self._failure_told.get(report.source)
        if last is not None and now - last < FAILURE_EVENT_EVERY:
            return
        self._failure_told[report.source] = now
        await self._tell(
            KIND_SOURCE_FAILED,
            Severity.WARNING,
            {
                "source": report.source,
                "status": report.status,
                "error": report.error,
                "retry_after": report.retry_after,
            },
            now,
        )

    async def _tell(
        self, kind: str, severity: Severity, detail: Mapping[str, object], now: datetime
    ) -> None:
        if self._events is None:
            return
        try:
            await asyncio.to_thread(self._events.record, kind, severity, detail, now)
        except Exception as error:
            log.warning("news system event not recorded: %s", one_line(error))


def _needs_reading(event: EconomicEvent, known: EventAnalysis | None, now: datetime) -> bool:
    if known is None or known.input_hash != input_hash(event):
        return True
    return known.source == "fallback" and now - known.analysed_at >= RETRY_FALLBACK_AFTER


def _worth_telling(event: EconomicEvent) -> bool:
    return event.impact is Impact.HIGH or (
        event.impact is Impact.MEDIUM and event.kind is not EventKind.HEADLINE
    )


def _summary_line(event: EconomicEvent, now: datetime) -> str:
    minutes = int((event.starts_at - now).total_seconds() // 60)
    when = f"dans {minutes} min" if minutes >= 0 else f"il y a {-minutes} min"
    figures = ""
    if event.forecast or event.previous or event.actual:
        figures = (
            f" (prévu {event.forecast or '?'}, précédent {event.previous or '?'}"
            + (f", publié {event.actual}" if event.actual else "")
            + ")"
        )
    major = " [majeure]" if is_major(event.title) else ""
    title = " ".join(event.title.split())[:SUMMARY_TITLE_CHARS]
    return (
        f"- {event.starts_at:%H:%M} UTC ({when}) {event.currency} {title} "
        f"[{event.impact.value}]{major}{figures}"
    )
