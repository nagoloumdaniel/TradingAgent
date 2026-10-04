"""Strategy quarantine kept in the database, so a restart does not lift it."""

from collections.abc import Callable
from datetime import UTC, datetime

from tradingagent.core.halt import pair_scope
from tradingagent.core.states import HaltAction, HaltSource
from tradingagent.storage.halts import HaltCommand, HaltStore


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PersistentQuarantine:
    def __init__(
        self,
        store: HaltStore,
        now: Callable[[], datetime] = _utc_now,
        operator_source: HaltSource = HaltSource.TELEGRAM,
    ) -> None:
        self._store = store
        self._now = now
        self._operator_source = operator_source

    def is_quarantined(self, ref: str, symbol: str) -> bool:
        # Fails closed like every halt read: an unreadable state keeps the pair stopped.
        return self._store.is_halted(pair_scope(ref, symbol))

    def quarantined(self) -> set[tuple[str, str]]:
        return self._store.halted_pairs()

    def quarantine(self, ref: str, symbol: str, reason: str, at: datetime) -> None:
        self._store.issue(
            HaltCommand(
                pair_scope(ref, symbol), HaltAction.HALT, HaltSource.AUTOMATIC, reason, "agent", at
            )
        )

    def rearm(self, ref: str, symbol: str, actor: str) -> None:
        self._store.issue(
            HaltCommand(
                pair_scope(ref, symbol),
                HaltAction.RESUME,
                self._operator_source,
                "re-armed by the operator",
                actor,
                self._now(),
            )
        )
