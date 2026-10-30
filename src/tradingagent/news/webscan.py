"""Calendar read from public web pages by the AI model (DeepSeek), no third-party calendar API.

The agent downloads a few public schedule pages (Fed, ECB, BEA, a market calendar), reduces each
to plain text, and asks the model to list the announcements it finds. The model never browses by
itself: it only reads text this module fetched, from hosts listed in `SCAN_PAGES`.

Two rules keep the model from inventing a calendar:

- a figure (actual, forecast, previous) is kept only when its text appears in the page. A figure
  the page does not carry is dropped, and the dashboard then hides it;
- an announcement without a clear time, or outside the window, is dropped.

X/Twitter is not scanned: it has no public read access without a paid API key.
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from typing import ClassVar

from tradingagent.core.mode import AiFilter
from tradingagent.news.analyst import CallJournal, NewsModel, _cost_eur, _json_body
from tradingagent.news.model import (
    EconomicEvent,
    EventKind,
    Impact,
    event_key,
    is_relevant,
    sanitize_event,
)
from tradingagent.news.ports import FetchResult, HttpClient, HttpError, SourceReport
from tradingagent.resilience import one_line
from tradingagent.storage.ai_calls import AiCall

log = logging.getLogger(__name__)

PURPOSE = "news_web_scan"
MAX_TEXT_CHARS = 28_000
MAX_EVENTS_PER_PAGE = 60
WINDOW_PAST = timedelta(hours=12)


@dataclass(frozen=True)
class ScanPage:
    name: str
    url: str
    label: str


SCAN_PAGES: tuple[ScanPage, ...] = (
    ScanPage(
        "web_fed", "https://www.federalreserve.gov/newsevents/calendar.htm", "Federal Reserve"
    ),
    ScanPage(
        "web_ecb",
        "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html",
        "Banque centrale européenne",
    ),
    ScanPage("web_bea", "https://www.bea.gov/news/schedule", "Bureau of Economic Analysis"),
    ScanPage("web_te", "https://tradingeconomics.com/calendar", "Trading Economics"),
)

_SYSTEM = (
    "You extract a macro-economic calendar from the text of a web page. Reply with one JSON "
    "object only. Use ONLY what the text says: never add an event from memory, never guess a "
    "figure. If the text does not give a forecast, previous or actual value, use null."
)


class _Text(HTMLParser):
    _SKIP: ClassVar[frozenset[str]] = frozenset({"script", "style", "noscript", "svg", "head"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._depth += 1
        elif tag in {"tr", "li", "p", "div", "br", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._depth:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._depth and data.strip():
            self.parts.append(data.strip() + " ")


def page_text(html: str) -> str:
    """Visible text of a page, one line per block, bounded."""
    parser = _Text()
    try:
        parser.feed(html)
    except Exception:  # malformed HTML: keep what was read
        log.debug("html parse stopped early")
    text = re.sub(r"[ \t]+", " ", "".join(parser.parts))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text[:MAX_TEXT_CHARS]


def _prompt(page: ScanPage, text: str, now: datetime, horizon_days: int) -> str:
    end = now + timedelta(days=horizon_days)
    return (
        f"Source: {page.label} ({page.url}). Now: {now:%Y-%m-%d %H:%M} UTC.\n"
        f"List the announcements dated between {now:%Y-%m-%d} and {end:%Y-%m-%d} that can move "
        "gold (XAUUSD) or bitcoin (BTCUSD): central-bank decisions and minutes, speeches and "
        "press conferences of central-bank chairs or finance ministers, CPI/PCE/jobs/GDP/retail "
        "releases, treasury auctions, crypto regulation decisions.\n"
        'Reply: {"events":[{"starts_at_utc":"YYYY-MM-DDTHH:MM:00Z","currency":"USD|EUR|GBP|JPY|'
        'CNY|CHF|CAD|AUD|NZD|BTC|XAU","title":"...","impact":"high|medium|low",'
        '"kind":"release|speech","forecast":null,"previous":null,"actual":null}]}\n'
        "Convert the page's time zone to UTC. Skip any event whose time the text does not give. "
        f"At most {MAX_EVENTS_PER_PAGE} events.\n\nPAGE TEXT:\n{text}"
    )


_IMPACTS = {"high": Impact.HIGH, "medium": Impact.MEDIUM, "low": Impact.LOW}


def _figure(value: object, text: str) -> str | None:
    """A figure the page really carries, else None."""
    if value is None:
        return None
    figure = str(value).strip()
    if not figure or figure.lower() in {"null", "none", "n/a", "-", "—"}:
        return None
    return figure if figure in text else None


def parse_events(
    raw: object, page: ScanPage, text: str, now: datetime, horizon_days: int
) -> tuple[EconomicEvent, ...]:
    rows = raw.get("events") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return ()
    low, high = now - WINDOW_PAST, now + timedelta(days=horizon_days + 1)
    events: dict[str, EconomicEvent] = {}
    for row in rows[:MAX_EVENTS_PER_PAGE]:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        currency = str(row.get("currency") or "").strip().upper()
        stamp = str(row.get("starts_at_utc") or "").strip().replace("Z", "+00:00")
        try:
            starts_at = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if starts_at.utcoffset() is None or not title or not currency:
            continue
        starts_at = starts_at.astimezone(UTC)
        if not low <= starts_at <= high:
            continue
        impact = _IMPACTS.get(str(row.get("impact") or "").lower(), Impact.LOW)
        kind = EventKind.SPEECH if str(row.get("kind")).lower() == "speech" else EventKind.RELEASE
        event = sanitize_event(
            EconomicEvent(
                key=event_key("web", currency, title, starts_at),
                starts_at=starts_at,
                currency=currency,
                title=title,
                impact=impact,
                kind=kind,
                country=currency,
                forecast=_figure(row.get("forecast"), text),
                previous=_figure(row.get("previous"), text),
                actual=_figure(row.get("actual"), text),
                source="web",
                url=page.url,
            )
        )
        if event is not None and is_relevant(event):
            events[event.key] = event
    return tuple(sorted(events.values(), key=lambda e: (e.starts_at, e.title)))


class WebScanSource:
    """One public page, read by the model. Scanned at most every `every_hours`."""

    def __init__(
        self,
        page: ScanPage,
        http: HttpClient,
        client: NewsModel | None,
        calls: CallJournal,
        *,
        model: str,
        horizon_days: int = 7,
        every_hours: float = 6.0,
        timeout: float = 20.0,
        ai_timeout: float = 90.0,
    ) -> None:
        self.name = page.name
        self._page = page
        self._http = http
        self._client = client
        self._calls = calls
        self._model = model
        self._horizon = horizon_days
        self._every = timedelta(hours=every_hours)
        self._timeout = timeout
        self._ai_timeout = ai_timeout
        self._last_scan: datetime | None = None

    def _report(
        self,
        now: datetime,
        ok: bool,
        items: int = 0,
        error: str | None = None,
        note: str | None = None,
    ) -> FetchResult:
        return FetchResult((), SourceReport(self.name, ok, items, now, error=error, note=note))

    async def fetch(self, now: datetime) -> FetchResult:
        if self._client is None:
            return self._report(now, True, note="IA non configurée : page non lue")
        if self._last_scan is not None and now - self._last_scan < self._every:
            return self._report(now, True, note="lecture récente, prochaine plus tard")
        self._last_scan = now  # a failed scan is not retried every poll
        try:
            response = await self._http.get(self._page.url, timeout=self._timeout)
        except HttpError as error:
            return self._report(now, False, error=one_line(error))
        if response.status != 200:
            return self._report(now, False, error=f"HTTP {response.status}")
        text = page_text(response.body.decode("utf-8", errors="replace"))
        if len(text) < 200:
            return self._report(now, False, error="page vide ou illisible")
        prompt = _prompt(self._page, text, now, self._horizon)
        started = time.monotonic()
        try:
            reply = await asyncio.wait_for(
                self._client.complete(_SYSTEM, prompt, max_tokens=4000), self._ai_timeout
            )
            raw = _json_body(reply.text)
        except (Exception, json.JSONDecodeError) as error:
            await self._journal(now, prompt, None, one_line(error), started, None)
            return self._report(now, False, error=f"lecture IA : {one_line(error)}")
        await self._journal(now, prompt, reply.text, None, started, _cost_eur(reply))
        events = parse_events(raw, self._page, text, now, self._horizon)
        return FetchResult(events, SourceReport(self.name, True, len(events), now))

    async def _journal(
        self,
        now: datetime,
        prompt: str,
        response: str | None,
        error: str | None,
        started: float,
        cost: Decimal | None,
    ) -> None:
        call = AiCall(
            signal_id=None,
            purpose=PURPOSE,
            ai_filter=AiFilter.SHADOW,
            request={"system": _SYSTEM, "prompt": prompt[:3000]}
            if error is None
            else {"error": error},
            response=response,
            verdict=None,
            error=error,
            latency_ms=int((time.monotonic() - started) * 1000),
            cost_eur=cost,
            called_at=now,
            model=self._model,
        )
        try:
            await asyncio.to_thread(self._calls.record, call)
        except Exception as failure:
            log.warning("web scan AI call not journalled: %s", one_line(failure))
