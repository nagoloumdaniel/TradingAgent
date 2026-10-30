"""The scheduled calendar: the public ForexFactory weekly export published by Faireconomy.

Verified 2026-10-30: `ff_calendar_thisweek.json` answers 200 (a JSON array, ~100 events a week);
`ff_calendar_nextweek.json` answers 404 until the next week is published (Fridays included), and
the host answers 429 with a `Retry-After` of about five minutes when polled too fast. All three
are handled: 404 on the optional week is a note, 429 is reported with its delay, and a body that
is not JSON (an HTML error page served with 200) is an error, never an empty calendar.

The feed carries no `actual` figure: it is a schedule with consensus and previous values. The
model and the screens support an actual; this source simply never fills it.

Dates are ISO strings with the New York offset; they are converted to UTC here and nowhere else.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from tradingagent.news.model import (
    EconomicEvent,
    EventKind,
    Impact,
    event_key,
    is_relevant,
    sanitize_event,
)
from tradingagent.news.ports import EMPTY_CALENDAR, FetchResult, HttpClient, HttpError, SourceReport

log = logging.getLogger(__name__)

THIS_WEEK_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEXT_WEEK_URL = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"

_IMPACTS = {
    "high": Impact.HIGH,
    "medium": Impact.MEDIUM,
    "low": Impact.LOW,
    # A bank holiday matters (thin liquidity) but moves nothing by itself.
    "holiday": Impact.LOW,
}
_SPEECH_MARKERS = ("speaks", "speech", "testif", "press conference")
DEFAULT_RETRY_AFTER = 300


class FaireconomySource:
    def __init__(
        self,
        http: HttpClient,
        *,
        url: str = THIS_WEEK_URL,
        name: str = "faireconomy",
        optional: bool = False,
        timeout: float = 15.0,
    ) -> None:
        self._http = http
        self._url = url
        self.name = name
        self._optional = optional
        self._timeout = timeout

    async def fetch(self, now: datetime) -> FetchResult:
        try:
            response = await self._http.get(self._url, timeout=self._timeout)
        except HttpError as error:
            return self._failure(now, None, f"réseau : {error}")
        if response.status == 404 and self._optional:
            note = "semaine suivante non encore publiée"
            report = SourceReport(self.name, True, 0, now, status=404, note=note)
            return FetchResult((), report)
        if response.status == 429:
            delay = _retry_after(response.header("Retry-After"))
            return self._failure(
                now, 429, f"limite de débit atteinte, nouvel essai dans {delay} s", delay
            )
        if response.status != 200:
            return self._failure(now, response.status, f"statut HTTP {response.status}")
        try:
            payload = json.loads(response.body)
        except ValueError:
            return self._failure(now, 200, "réponse non JSON (page d'erreur ?)")
        if not isinstance(payload, list):
            return self._failure(now, 200, "format inattendu : tableau JSON attendu")
        events = tuple(self._parse(payload))
        if not events and not self._optional and now.weekday() < 5:
            # A weekday export with nothing for gold or bitcoin is a broken export, not a quiet
            # week (the dollar alone has a release most days): it must not count as a refresh.
            return self._failure(
                now, 200, f"{EMPTY_CALENDAR} : réponse 200 sans aucune annonce pertinente"
            )
        covers = (
            (min(e.starts_at for e in events), max(e.starts_at for e in events)) if events else None
        )
        report = SourceReport(self.name, True, len(events), now, status=200)
        return FetchResult(events, report, covers)

    def _failure(
        self, now: datetime, status: int | None, error: str, retry_after: int | None = None
    ) -> FetchResult:
        log.warning("news source %s failed: %s", self.name, error)
        report = SourceReport(
            self.name, False, 0, now, status=status, error=error, retry_after=retry_after
        )
        return FetchResult((), report)

    def _parse(self, payload: list[Any]) -> list[EconomicEvent]:
        events: dict[str, EconomicEvent] = {}
        for row in payload:
            event = self._event(row)
            event = sanitize_event(event) if event is not None else None
            if event is not None and is_relevant(event):
                events[event.key] = event
        return sorted(events.values(), key=lambda e: (e.starts_at, e.title))

    def _event(self, row: Any) -> EconomicEvent | None:
        if not isinstance(row, dict):
            return None
        title = str(row.get("title") or "").strip()
        currency = str(row.get("country") or "").strip().upper()
        raw_date = row.get("date")
        if not title or not currency or not isinstance(raw_date, str):
            return None
        try:
            starts_at = datetime.fromisoformat(raw_date)
        except ValueError:
            return None
        if starts_at.utcoffset() is None:
            # The feed always carries an offset; a bare time would be a guess about the zone.
            return None
        starts_at = starts_at.astimezone(UTC)
        impact = _IMPACTS.get(str(row.get("impact") or "").strip().lower(), Impact.LOW)
        lowered = title.lower()
        kind = (
            EventKind.SPEECH
            if any(marker in lowered for marker in _SPEECH_MARKERS)
            else EventKind.RELEASE
        )
        return EconomicEvent(
            key=event_key(self.name, currency, title, starts_at),
            starts_at=starts_at,
            currency=currency,
            title=title,
            impact=impact,
            kind=kind,
            country=currency,
            forecast=_figure(row.get("forecast")),
            previous=_figure(row.get("previous")),
            actual=_figure(row.get("actual")),
            source=self.name,
            url=str(row["url"]) if isinstance(row.get("url"), str) and row["url"] else None,
        )


def _figure(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _retry_after(header: str | None) -> int:
    try:
        seconds = int(header) if header is not None else DEFAULT_RETRY_AFTER
    except ValueError:
        return DEFAULT_RETRY_AFTER
    return max(60, min(seconds, 3600))
