"""Server-Sent Events for the live pages (§23, §32, §45).

The agent writes to the database; the dashboard polls it and pushes what changed. There is
no message broker to operate, no websocket to authenticate, and the same database the agent
already uses is the only dependency — which is the whole reason this stack was chosen.

The stream is bounded when a caller asks for it (``cycles``), so ``TestClient`` can consume
it to completion instead of hanging on an endless response. It also carries the payloads
already formatted by :mod:`tradingagent.web.views`, so the browser never formats, let alone
computes, a figure.
"""

import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Engine

from tradingagent.core.states import Severity
from tradingagent.web import format as display
from tradingagent.web import paging
from tradingagent.web.queries import open_positions, recent_events
from tradingagent.web.views import (
    ALERT_COLUMNS,
    POSITION_COLUMNS,
    alert_cells,
    alert_classes,
    position_cells,
)

# Sent once, first: it tells EventSource how long to wait before reconnecting.
RETRY_HINT = "retry: 3000\n\n"
DEFAULT_INTERVAL_SECONDS = 2.0
DEFAULT_ALERT_LIMIT = 30


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Frame:
    """One SSE event, rendered exactly as the wire format requires."""

    event: str
    data: str

    def render(self) -> str:
        return f"event: {self.event}\ndata: {self.data}\n\n"


class EventStream:
    """Polls the database and yields the frames a connected client receives.

    ``now`` and ``sleep`` are injected: a test drives the stream without waiting, and the
    page never reads the clock of its own accord.
    """

    def __init__(
        self,
        engine: Engine,
        *,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
        alert_limit: int = DEFAULT_ALERT_LIMIT,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("the polling interval must be strictly positive")
        self._engine = engine
        self._interval = interval_seconds
        self._now = now if now is not None else _utc_now
        self._sleep = sleep if sleep is not None else time.sleep
        self._alert_limit = alert_limit

    def poll(
        self, at: datetime, since: datetime | None, market: str = paging.ALL_MARKETS
    ) -> list[Frame]:
        """One cycle: the open positions of one market, then the alerts recorded after ``since``.

        ``market`` is the page's own filter, carried into the stream: a live count that
        ignored it would contradict the table it sits above. Everything the frame carries is
        already formatted by :mod:`tradingagent.web.views` and :mod:`tradingagent.web.format`,
        including the timestamp — the browser writes nothing of its own, so the line rendered
        on page load and the line pushed a second later read identically.
        """
        positions = open_positions(self._engine, at, market=market)
        alerts = recent_events(
            self._engine, since=since, minimum=Severity.WARNING, limit=self._alert_limit
        )
        # Stored newest-first; the frame is sent oldest-first, because the client inserts a
        # frame at the top in reverse and the list then reads newest-first throughout.
        ordered = list(reversed(alerts))
        rendered_at = display.precise(at)
        return [
            Frame(
                "positions",
                json.dumps(
                    {
                        "at": rendered_at,
                        "columns": list(POSITION_COLUMNS),
                        "rows": [position_cells(position) for position in positions],
                    },
                    ensure_ascii=False,
                ),
            ),
            Frame(
                "alerts",
                json.dumps(
                    {
                        "at": rendered_at,
                        "columns": list(ALERT_COLUMNS),
                        "rows": [alert_cells(event) for event in ordered],
                        # The badge class travels with the row: the browser colours what the
                        # server decided, it does not map a label back to a severity.
                        "severities": alert_classes(ordered),
                    },
                    ensure_ascii=False,
                ),
            ),
        ]

    def frames(
        self, *, cycles: int | None = None, market: str = paging.ALL_MARKETS
    ) -> Iterator[str]:
        """The stream body. ``cycles`` bounds it; ``None`` streams until the client leaves."""
        yield RETRY_HINT
        since: datetime | None = None
        produced = 0
        while cycles is None or produced < cycles:
            at = self._now()
            for frame in self.poll(at, since, market=market):
                yield frame.render()
            since = at
            produced += 1
            if cycles is not None and produced >= cycles:
                return
            self._sleep(self._interval)

    @property
    def interval_seconds(self) -> float:
        return self._interval


SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # nginx and friends must not buffer a stream that exists to arrive promptly.
    "X-Accel-Buffering": "no",
}

__all__ = ["DEFAULT_INTERVAL_SECONDS", "RETRY_HINT", "SSE_HEADERS", "EventStream", "Frame"]
