"""Automatic halts (TASK-036, action 3).

The guardian turns the conditions of the specification into halt commands. It halts, it
never lifts a global halt: that is the operator's decision. Only the connection halt of
RM-013 is lifted automatically, once the data is healthy again.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tradingagent.core.account import AccountModeMismatchError
from tradingagent.core.halt import CONNECTION, GLOBAL, HaltStatus
from tradingagent.core.states import HaltAction, HaltSource
from tradingagent.risk.breaches import hard_limit_breaches
from tradingagent.risk.model import PortfolioState, RiskLimits
from tradingagent.storage.halts import HaltCommand, HaltStore

log = logging.getLogger(__name__)

AGENT = "agent"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Guardian:
    def __init__(self, store: HaltStore, now: Callable[[], datetime] = _utc_now) -> None:
        self._store = store
        self._now = now

    def trading_status(self) -> HaltStatus:
        """To be read before any action that engages capital. Fails closed."""
        return self._store.status()

    def on_account_mismatch(self, error: AccountModeMismatchError) -> None:
        """RM-017: maximum severity, execution stops until the operator intervenes."""
        self._halt(GLOBAL, f"account does not match the mode (RM-017): {error}")

    def on_divergence(self, detail: str) -> None:
        """RM-014: never resolved automatically."""
        self._halt(GLOBAL, f"local state diverges from the account (RM-014): {detail}")

    def on_portfolio(self, equity: Decimal, portfolio: PortfolioState, limits: RiskLimits) -> bool:
        """Halt when the weekly loss or the drawdown limit is reached. True if halted."""
        breaches = hard_limit_breaches(equity, portfolio, limits)
        if breaches and not self._store.is_halted(GLOBAL):
            self._halt(GLOBAL, "; ".join(breaches))
        return bool(breaches)

    def on_connection_lost(self, since: datetime, threshold: timedelta) -> bool:
        """RM-013: suspend trading once the connection has been lost for too long."""
        if self._now() - since < threshold:
            return False
        if not self._store.is_halted(CONNECTION):
            self._halt(CONNECTION, f"connection lost since {since.isoformat()} (RM-013)")
        return True

    def on_data_healthy(self) -> None:
        """RM-013: resume only once the series are healthy again."""
        if self._store.is_halted(CONNECTION):
            self._store.issue(
                HaltCommand(
                    CONNECTION,
                    HaltAction.RESUME,
                    HaltSource.AUTOMATIC,
                    "connection restored and series healthy",
                    AGENT,
                    self._now(),
                )
            )

    def _halt(self, scope: str, reason: str) -> None:
        log.critical("halting %s: %s", scope, reason)
        self._store.issue(
            HaltCommand(scope, HaltAction.HALT, HaltSource.AUTOMATIC, reason, AGENT, self._now())
        )
