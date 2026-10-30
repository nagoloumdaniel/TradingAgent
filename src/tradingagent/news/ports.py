"""The seams of the news package: how bytes arrive, and what a source reports about itself.

A source never raises into the scheduler. A failed fetch is a `SourceReport` with `ok=False` and
a sentence the operator can read on the dashboard; the events already stored stay where they are.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from tradingagent.news.model import EconomicEvent

#: Opens the error of a fetch that answered 200 with no relevant announcement on a weekday: a
#: broken export looks exactly like a quiet calendar, so it is reported as a failure.
EMPTY_CALENDAR = "calendrier vide"


class HttpError(Exception):
    """The request did not produce a response at all: DNS, TLS, timeout, connection reset."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]

    def header(self, name: str) -> str | None:
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return None


class HttpClient(Protocol):
    async def get(self, url: str, *, timeout: float) -> HttpResponse: ...


@dataclass(frozen=True)
class SourceReport:
    """What one fetch of one feed amounted to. Stored, so the dashboard can say what is stale."""

    source: str
    ok: bool
    items: int
    fetched_at: datetime
    status: int | None = None
    error: str | None = None
    #: Informative, not an error: e.g. next week's calendar is not published yet.
    note: str | None = None
    #: Seconds the host asked us to wait (HTTP 429): the scheduler leaves the source alone.
    retry_after: int | None = None


@dataclass(frozen=True)
class FetchResult:
    events: tuple[EconomicEvent, ...]
    report: SourceReport
    #: The span this fetch is authoritative for: stored events of this source inside it that the
    #: fetch no longer lists have been withdrawn or rescheduled, and are removed. None for feeds
    #: that only ever append (headlines).
    covers: tuple[datetime, datetime] | None = None


class EventSource(Protocol):
    name: str

    async def fetch(self, now: datetime) -> FetchResult: ...
