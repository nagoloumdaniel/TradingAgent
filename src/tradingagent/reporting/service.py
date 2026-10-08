"""Report production and catch-up (F-022, TASK-042).

One `run(now)` finds every elapsed unreported window, generates the numeric report,
persists it, then sends it. The narrator, when wired, only appends commentary to the
finished text — it can add no number, and its failure costs nothing: the numeric report
is sent anyway.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy import Engine

from tradingagent.reporting.generator import ReportGenerator
from tradingagent.reporting.schedule import Period, missed_windows
from tradingagent.storage.account import AccountStore, ReportData
from tradingagent.storage.reports import ReportStore

log = logging.getLogger(__name__)

Narrator = Callable[[str], Awaitable[str]]
Sender = Callable[[str], Awaitable[None] | None]


class ReportService:
    def __init__(
        self,
        engine: Engine,
        sender: Sender,
        narrator: Narrator | None = None,
    ) -> None:
        self._engine = engine
        self._generator = ReportGenerator(ReportData(engine), AccountStore(engine))
        self._reports = ReportStore(engine)
        self._send = sender
        self._narrator = narrator

    async def run(self, now: datetime) -> list[str]:
        sent: list[str] = []
        for period in Period:
            last_end = await asyncio.to_thread(self._reports.latest_window_end, period.value)
            for window in missed_windows(period, last_end, now):
                content = await asyncio.to_thread(self._generator.build, window)
                market = await asyncio.to_thread(self._generator.sole_market, window)
                await asyncio.to_thread(
                    self._reports.save,
                    period.value,
                    window.start,
                    window.end,
                    content,
                    now,
                    market=market,
                )
                stored = await asyncio.to_thread(self._reports.find, period.value, window.start)
                if stored is None:
                    raise RuntimeError(f"report for {window.start} was not persisted")
                full = content
                if self._narrator is not None:
                    try:
                        narrative = await self._narrator(content)
                        full = f"{content}\n\n— Commentaire —\n{narrative}"
                    except Exception as error:
                        log.warning("narrator unavailable, numeric report sent alone: %s", error)
                result = self._send(full)
                if asyncio.iscoroutine(result):
                    await result
                await asyncio.to_thread(self._reports.mark_sent, stored.id, now)
                sent.append(full)
        return sent
