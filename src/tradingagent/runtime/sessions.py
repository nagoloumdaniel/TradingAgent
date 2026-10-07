"""Market sessions: what the agent stops while a market is closed, and reopens by itself.

Gold closes on Friday evening and reopens on Sunday night; a crypto CFD quotes around the
clock. The learned calendar already knows both (TASK-014), so this module invents no second
notion of trading hours: it turns the calendar's answer into two operator-visible effects and
nothing else.

* a halt in the `session:<symbol>` scope, which the agent owns and lifts on its own once the
  calendar says the market trades again. It is deliberately not `market:<symbol>`, which the
  operator owns through `/disable`: an automatic weekend resume must never undo that.
* one Telegram announcement per closure, through the alert layer's episode latch.

Detection itself lives in `data.market_calendar` (`is_open`, `closure_started_at`), pure and
testable by injecting a date; this class only carries the answer to the halt store and the
notifier.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from tradingagent.core.halt import session_scope
from tradingagent.core.states import HaltAction, HaltSource
from tradingagent.data.market_calendar import MarketCalendar, closure_started_at, is_open
from tradingagent.notify.health_alerts import HealthAlerter
from tradingagent.storage.halts import HaltCommand, HaltStore

log = logging.getLogger(__name__)

AGENT = "agent"


@dataclass(frozen=True)
class SessionOutcome:
    """What the guard did, or would have refused to do, for one market on one cycle."""

    symbol: str
    closed: bool
    changed: bool
    reason: str = ""


def closure_reason(symbol: str, started: datetime) -> str:
    """The halt reason doubles as the closure's identity, so a restart can recognise it."""
    return f"{symbol} market closed since {started.isoformat()}"


class MarketSessionGuard:
    def __init__(
        self,
        *,
        halts: HaltStore,
        alerts: HealthAlerter,
        calendar_for: Callable[[str], MarketCalendar | None],
    ) -> None:
        self._halts = halts
        self._alerts = alerts
        self._calendar_for = calendar_for

    async def guard(self, symbol: str, at: datetime) -> SessionOutcome | None:
        """Stop the market if `at` falls in a closure, resume it if it trades again.

        Returns None when there is nothing to say: a seven-day market, or an unknown
        calendar. Never raises for a normal calendar answer.
        """
        calendar = self._calendar_for(symbol)
        if calendar is None or calendar.always_open:
            return None
        if is_open(calendar, at):
            return await self._resume(symbol, at)
        return await self._close(symbol, calendar, at)

    async def _close(
        self, symbol: str, calendar: MarketCalendar, at: datetime
    ) -> SessionOutcome | None:
        started = closure_started_at(calendar, at)
        if started is None:  # the caller only reaches here on a closed slot
            return None
        reason = closure_reason(symbol, started)
        scope = session_scope(symbol)
        if self._already_closed(scope, reason):
            return SessionOutcome(symbol, closed=True, changed=False, reason=reason)
        self._halts.issue(
            HaltCommand(
                scope,
                HaltAction.HALT,
                HaltSource.AUTOMATIC,
                reason,
                AGENT,
                at,
            )
        )
        log.info("%s session halted: %s", symbol, reason)
        await self._alerts.market_closed(symbol, started, at)
        return SessionOutcome(symbol, closed=True, changed=True, reason=reason)

    async def _resume(self, symbol: str, at: datetime) -> SessionOutcome | None:
        scope = session_scope(symbol)
        if not self._halts.is_halted(scope):
            return None
        self._halts.issue(
            HaltCommand(
                scope,
                HaltAction.RESUME,
                HaltSource.AUTOMATIC,
                f"{symbol} market reopened",
                AGENT,
                at,
            )
        )
        log.info("%s session resumed: the market trades again", symbol)
        await self._alerts.market_reopened(symbol, at)
        return SessionOutcome(symbol, closed=False, changed=True, reason="market reopened")

    def _already_closed(self, scope: str, reason: str) -> bool:
        """True when this very closure is already halted and announced.

        The reason carries the closure's start, so a restart in the middle of a weekend
        recognises the episode instead of announcing it again.
        """
        latest = self._halts.history(scope, limit=1)
        return bool(latest) and latest[0].action is HaltAction.HALT and latest[0].reason == reason
