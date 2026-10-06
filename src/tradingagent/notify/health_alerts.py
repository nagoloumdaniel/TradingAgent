"""Health alerting for the operator (F-024, TASK-024).

The runtime reports what it observes (starts, stops, outages, failures, series health,
disk pressure); this class decides when an observation deserves a Telegram message and
limits the repetition of the same alert: one condition, one message, and a reminder only
after the cooldown. Every alert is also written to the system-event ledger.

The sender is injected: the Telegram adapter arrives with the loop wiring (TASK-034).
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path

from tradingagent.core.states import Severity
from tradingagent.storage.events import SystemEventStore

Sender = Callable[[str], Awaitable[None]]

HEALTHY = "HEALTHY"
MARKET_CLOSED = "MARKET_CLOSED"


def _utc(stamp: datetime) -> str:
    return f"{stamp:%Y-%m-%d %H:%M} UTC"


class HealthAlerter:
    def __init__(
        self,
        send: Sender,
        events: SystemEventStore,
        *,
        repeat_after: timedelta = timedelta(minutes=30),
        failure_threshold: int = 3,
        degraded_threshold: int = 2,
        disk_alert_percent: float = 90.0,
        disk_path: Path | None = None,
    ) -> None:
        self._send = send
        self._events = events
        self._repeat_after = repeat_after
        self._failure_threshold = failure_threshold
        self._degraded_threshold = degraded_threshold
        self._disk_alert_percent = disk_alert_percent
        self._disk_path = disk_path
        self._last_sent: dict[str, datetime] = {}
        self._failures: dict[str, int] = {}
        self._degraded_run: dict[str, int] = {}

    async def process_started(self, at: datetime, mode: str) -> None:
        await self._alert(
            "start",
            Severity.INFO,
            f"Agent démarré en mode {mode} à {_utc(at)}.",
            at,
        )

    async def process_stopping(self, at: datetime) -> None:
        await self._alert("stop", Severity.INFO, f"Agent arrêté à {_utc(at)}.", at)

    async def connection_lost(self, component: str, at: datetime) -> None:
        await self._alert(
            f"outage:{component}",
            Severity.CRITICAL,
            f"Coupure : {component} est injoignable depuis {_utc(at)}.",
            at,
        )

    async def connection_restored(self, component: str, at: datetime) -> None:
        # The next outage is a new episode: it must alert again despite the cooldown.
        self._last_sent.pop(f"outage:{component}", None)
        await self._alert(
            f"recovery:{component}",
            Severity.INFO,
            f"Connexion {component} rétablie à {_utc(at)}.",
            at,
        )

    async def component_failure(self, component: str, detail: str, at: datetime) -> None:
        count = self._failures.get(component, 0) + 1
        self._failures[component] = count
        if count < self._failure_threshold:
            return
        await self._alert(
            f"failure:{component}",
            Severity.WARNING,
            f"{component} a échoué {count} fois de suite : {detail}",
            at,
        )

    async def component_recovered(self, component: str, at: datetime) -> None:
        self._failures.pop(component, None)

    async def series_check(self, symbol: str, status: str, at: datetime) -> None:
        if status in (HEALTHY, MARKET_CLOSED):
            self._degraded_run.pop(symbol, None)
            return
        run = self._degraded_run.get(symbol, 0) + 1
        self._degraded_run[symbol] = run
        if run < self._degraded_threshold:
            return
        if run == self._degraded_threshold:
            # Persistence is a new episode: it must alert again despite the cooldown.
            self._last_sent.pop(f"series:{symbol}", None)
        await self._alert(
            f"series:{symbol}",
            Severity.WARNING,
            f"Série {symbol} dégradée de façon persistante : {status}.",
            at,
        )

    async def disk_pressure(self, path: str, used_percent: float, at: datetime) -> None:
        if used_percent < self._disk_alert_percent:
            return
        await self._alert(
            "disk",
            Severity.CRITICAL,
            f"Disque {path} presque saturé : {used_percent:.1f} % utilisés.",
            at,
        )

    async def _alert(self, key: str, severity: Severity, text: str, at: datetime) -> None:
        last = self._last_sent.get(key)
        if last is not None and at - last < self._repeat_after:
            return
        self._last_sent[key] = at
        await asyncio.to_thread(
            self._events.record, "health_alert", severity, {"key": key, "text": text}, at
        )
        await self._send(text)
