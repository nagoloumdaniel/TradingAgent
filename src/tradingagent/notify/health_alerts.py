"""Health alerting for the operator (F-024, TASK-024).

The runtime reports what it observes (starts, stops, outages, failures, series health,
disk pressure); this class decides when an observation deserves a Telegram message and
limits the repetition of the same alert: one condition, one message, and a reminder only
after the cooldown. Every alert is also written to the system-event ledger.

These messages are sent *without* a parse mode (the alert sender does not set one), so they
are plain text: no HTML, and no escaping that would surface as `&amp;`. Structure therefore
comes from blank lines and from short lines only. Each alert answers three questions — what
happened, where, and what the agent does now — and none of them says just "erreur".

The sender is injected: the Telegram adapter arrives with the loop wiring (TASK-034).
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path

from tradingagent.core.states import Severity
from tradingagent.storage.events import SystemEventStore

Sender = Callable[[str], Awaitable[None]]

HEALTHY = "HEALTHY"
MARKET_CLOSED = "MARKET_CLOSED"

# A component name is an internal word; the operator reads what it drives.
COMPONENT_LABELS = {
    "terminal": "terminal MT5",
    "market_data": "données de marché",
    "server_clock": "horloge du serveur",
    "reconciliation": "réconciliation des positions",
}
DEFAULT_IMPACT = "L'agent continue de tourner et réessaie."
COMPONENT_IMPACTS = {
    "terminal": "Aucun nouvel ordre tant que la liaison n'est pas rétablie.",
    "server_clock": "Aucun signal n'est émis tant que l'horloge n'est pas vérifiée.",
    "reconciliation": "Le trading reste suspendu jusqu'à ton arbitrage (RM-014).",
    "market_data": "Les signaux peuvent être refusés tant que l'échec dure.",
}

# A detail coming from an exception can carry a path or a URL; the operator gets neither.
_URL = re.compile(r"https?://\S+")
_WINDOWS_PATH = re.compile(r"[A-Za-z]:\\[^\s;,]*")
_POSIX_PATH = re.compile(r"(?<![\w.])/(?:home|Users|var|etc|opt|tmp|usr)/[^\s;,]*")
_DRIVE = re.compile(r"^[A-Za-z]:")
REDACTED = "(retiré)"
DETAIL_LIMIT = 160


def _component(component: str) -> str:
    return COMPONENT_LABELS.get(component, component)


def _impact(component: str) -> str:
    return COMPONENT_IMPACTS.get(component, DEFAULT_IMPACT)


def _cause(detail: str) -> str:
    """The failure in one short, path-free line: what failed, not where the code lives."""
    text = " ".join(detail.split())
    for pattern in (_URL, _WINDOWS_PATH, _POSIX_PATH):
        text = pattern.sub(REDACTED, text)
    if len(text) <= DETAIL_LIMIT:
        return text
    return text[: DETAIL_LIMIT - 1] + "…"


def _disk_label(path: str) -> str:
    """A drive is enough to act on; the folders underneath are not the operator's business."""
    match = _DRIVE.match(path)
    return match.group(0) if match else path


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
        # Markets whose closure has been announced but not yet their reopening. A closure
        # lasts a weekend, the loop runs every twenty seconds: this is the one-message latch.
        self._closed_markets: dict[str, datetime] = {}

    async def process_started(self, at: datetime, mode: str) -> None:
        # One line, the action and nothing else — and no clock stamp: Telegram dates every
        # message it delivers, so repeating it is noise on a phone. The operator asked for it
        # gone from the notices the agent pushes. Anything that goes wrong keeps its detail.
        await self._alert(
            "start",
            Severity.INFO,
            f"🟢 Agent démarré · {mode}",
            at,
        )

    async def process_stopping(self, at: datetime) -> None:
        await self._alert(
            "stop",
            Severity.INFO,
            "🔴 Agent arrêté",
            at,
        )

    async def connection_lost(self, component: str, at: datetime) -> None:
        await self._alert(
            f"outage:{component}",
            Severity.CRITICAL,
            f"🔌 Coupure · {_component(component)}\n\n{_impact(component)}",
            at,
        )

    async def connection_restored(self, component: str, at: datetime) -> None:
        # The next outage is a new episode: it must alert again despite the cooldown.
        self._last_sent.pop(f"outage:{component}", None)
        await self._alert(
            f"recovery:{component}",
            Severity.INFO,
            f"✅ Connexion rétablie · {_component(component)}\n"
            f"\n"
            f"Les signaux et les ordres reprennent.",
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
            f"⚠️ Échec répété · {_component(component)}\n"
            f"\n"
            f"{count} échecs de suite : {_cause(detail)}\n"
            f"{_impact(component)}",
            at,
        )

    async def component_recovered(self, component: str, at: datetime) -> None:
        self._failures.pop(component, None)

    async def market_closed(self, symbol: str, since: datetime, at: datetime) -> None:
        """Announce a market's closure once per episode, never once per cycle.

        A gold weekend lasts two days while the loop runs every twenty seconds. The latch is
        released by `market_reopened`, so the next closure is announced again.
        """
        if symbol in self._closed_markets:
            return
        self._closed_markets[symbol] = since
        self._last_sent.pop(f"market_closed:{symbol}", None)
        await self._alert(
            f"market_closed:{symbol}",
            Severity.INFO,
            f"🌙 Marché fermé · {symbol}\n"
            f"\n"
            f"Aucun signal ni ordre sur {symbol} ; l'agent continue de tourner sur les "
            f"autres marchés et rouvrira {symbol} tout seul.",
            at,
        )

    async def market_reopened(self, symbol: str, at: datetime) -> None:
        """Confirm the automatic reopening, and re-arm the next closure's announcement.

        Unlike the closure, this is not latched on the announcement: after a restart the
        process remembers the closure through the persisted halt, not this dictionary, and
        the operator still deserves to learn that gold trades again. The caller (the session
        guard) only reaches it once per closure, when it lifts the halt.
        """
        self._closed_markets.pop(symbol, None)
        self._last_sent.pop(f"market_reopened:{symbol}", None)
        await self._alert(
            f"market_reopened:{symbol}",
            Severity.INFO,
            f"☀️ Marché rouvert · {symbol}\n\nLes signaux et les ordres reprennent sur {symbol}.",
            at,
        )

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
            f"⚠️ Série dégradée · {symbol}\n"
            f"\n"
            f"État {status} confirmé sur {run} contrôles de suite.\n"
            f"L'agent continue de tourner ; les signaux de ce marché peuvent être refusés.",
            at,
        )

    async def disk_pressure(self, path: str, used_percent: float, at: datetime) -> None:
        if used_percent < self._disk_alert_percent:
            return
        await self._alert(
            "disk",
            Severity.CRITICAL,
            f"🪫 Disque presque plein\n"
            f"\n"
            f"Disque {_disk_label(path)} à {used_percent:.1f} % d'occupation.\n"
            f"L'agent continue ; libère de la place avant saturation.",
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
