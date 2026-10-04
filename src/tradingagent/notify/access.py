"""Who may talk to the bot, and how often (F-014, EF-011, TASK-020).

Pure and clock-free: every decision takes the time it is made at. A stranger is never told
anything; recording what strangers send is capped so they cannot fill the database.
"""

from collections import defaultdict, deque
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Verdict(StrEnum):
    ALLOWED = "allowed"
    UNAUTHORIZED = "unauthorized"
    NOT_PRIVATE = "not_private"
    RATE_LIMITED = "rate_limited"


@dataclass(frozen=True)
class AccessDecision:
    verdict: Verdict
    record: bool  # worth an audit row: false for the repeats a flood would generate
    newly_limited: bool = False


class AccessGate:
    def __init__(
        self,
        allowed_ids: Collection[int],
        max_attempts: int = 10,
        window: timedelta = timedelta(minutes=1),
        cooldown: timedelta = timedelta(minutes=5),
        max_unauthorized_records_per_hour: int = 100,
    ) -> None:
        self._allowed = frozenset(allowed_ids)
        self._max_attempts = max_attempts
        self._window = window
        self._cooldown = cooldown
        self._unauthorized_budget = max_unauthorized_records_per_hour
        self._attempts: dict[int, deque[datetime]] = defaultdict(deque)
        self._blocked_until: dict[int, datetime] = {}
        self._unauthorized_records: deque[datetime] = deque()

    def check(self, user_id: int, private_chat: bool, at: datetime) -> AccessDecision:
        blocked_until = self._blocked_until.get(user_id)
        if blocked_until is not None:
            if at < blocked_until:
                return AccessDecision(Verdict.RATE_LIMITED, record=False)
            del self._blocked_until[user_id]

        attempts = self._attempts[user_id]
        while attempts and at - attempts[0] >= self._window:
            attempts.popleft()
        attempts.append(at)
        if len(attempts) > self._max_attempts:
            attempts.clear()
            self._blocked_until[user_id] = at + self._cooldown
            return AccessDecision(Verdict.RATE_LIMITED, record=True, newly_limited=True)

        if user_id not in self._allowed:
            return AccessDecision(Verdict.UNAUTHORIZED, record=self._spend_unauthorized(at))
        if not private_chat:
            return AccessDecision(Verdict.NOT_PRIVATE, record=True)
        return AccessDecision(Verdict.ALLOWED, record=True)

    def is_operator(self, user_id: int) -> bool:
        return user_id in self._allowed

    def _spend_unauthorized(self, at: datetime) -> bool:
        records = self._unauthorized_records
        while records and at - records[0] >= timedelta(hours=1):
            records.popleft()
        if len(records) >= self._unauthorized_budget:
            return False
        records.append(at)
        return True
