"""Reconciliation of the local state against the account (TASK-083, F-017, RM-014).

Once per cycle the local ledger and the broker are compared. Any difference — a position
the base has and the account does not, a position the account has and the base does not, a
volume or a stop that drifted — suspends trading and alerts. **Nothing is ever corrected
automatically**: RM-014 has no exception, so only the operator resolves a divergence.

`reconcile_state` is pure and returns the descriptions; `Reconciler` is the wired form that
turns a non-empty result into a global halt through the guardian.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from tradingagent.control.guardian import Guardian
from tradingagent.core.mode import TradingMode
from tradingagent.execution.ports import LocalPosition, TradeLog
from tradingagent.risk.model import BrokerPosition

log = logging.getLogger(__name__)

STOP_TOLERANCE = Decimal("0.01")
VOLUME_TOLERANCE = Decimal("0.000001")


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Divergence:
    """One difference. `kind` is a stable label, `detail` is for the operator."""

    kind: str
    ticket: int | None
    detail: str


@dataclass(frozen=True)
class Reconciliation:
    checked_at: datetime
    mode: TradingMode
    local_count: int
    broker_count: int
    divergences: tuple[Divergence, ...]

    @property
    def balanced(self) -> bool:
        return not self.divergences

    def summary(self) -> str:
        if self.balanced:
            return f"coherent: {self.local_count} local, {self.broker_count} broker"
        lines = [
            f"{len(self.divergences)} divergence(s): {self.local_count} local, "
            f"{self.broker_count} broker"
        ]
        lines.extend(f"- {item.kind} {item.ticket}: {item.detail}" for item in self.divergences)
        return "\n".join(lines)


def reconcile_state(
    log_: TradeLog, mode: TradingMode, broker_positions: tuple[BrokerPosition, ...]
) -> tuple[str, ...]:
    """Descriptions of every difference, empty when the two states agree."""
    local = {position.ticket: position for position in log_.local_positions(mode)}
    remote = {position.ticket: position for position in broker_positions}
    divergences: list[str] = []
    for ticket in sorted(set(local) - set(remote)):
        found_locally = local[ticket]
        divergences.append(
            f"position {ticket} {found_locally.symbol} is open locally but absent at the broker"
        )
    for ticket in sorted(set(remote) - set(local)):
        known_to_broker = remote[ticket]
        divergences.append(
            f"position {ticket} {known_to_broker.symbol} exists at the broker but not locally"
        )
    for ticket in sorted(set(local) & set(remote)):
        divergences.extend(_compare(local[ticket], remote[ticket]))
    return tuple(divergences)


def _compare(local: LocalPosition, remote: BrokerPosition) -> list[str]:
    found: list[str] = []
    if local.symbol != remote.symbol:
        found.append(
            f"position {local.ticket} symbol {local.symbol} locally, {remote.symbol} broker"
        )
    if local.direction is not remote.direction:
        found.append(
            f"position {local.ticket} side {local.direction.value} locally, "
            f"{remote.direction.value} broker"
        )
    if abs(local.volume - remote.volume) > VOLUME_TOLERANCE:
        found.append(
            f"position {local.ticket} volume {local.volume} locally, {remote.volume} broker"
        )
    if not _same_price(local.stop_loss, remote.stop_loss):
        found.append(
            f"position {local.ticket} stop {local.stop_loss} locally, {remote.stop_loss} broker"
        )
    return found


def _same_price(local: Decimal | None, remote: Decimal | None) -> bool:
    if local is None or remote is None:
        return local is None and remote is None
    return abs(local - remote) <= STOP_TOLERANCE


class Reconciler:
    """The wired reconciliation: a divergence halts trading and alerts, and that is all."""

    def __init__(
        self,
        log_: TradeLog,
        *,
        mode: TradingMode,
        guardian: Guardian,
        alert: Callable[[str], None] | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._log = log_
        self._mode = mode
        self._guardian = guardian
        self._alert = alert
        self._now = now

    async def check(self, broker: object) -> Reconciliation:
        positions = await _positions_of(broker)
        divergences = tuple(
            Divergence("state", None, detail)
            for detail in reconcile_state(self._log, self._mode, positions)
        )
        report = Reconciliation(
            checked_at=self._now(),
            mode=self._mode,
            local_count=len(self._log.local_positions(self._mode)),
            broker_count=len(positions),
            divergences=divergences,
        )
        if not report.balanced:
            self._guardian.on_divergence(report.summary())
            if self._alert is not None:
                self._alert(f"state divergence (RM-014), trading suspended:\n{report.summary()}")
        return report


async def _positions_of(broker: object) -> tuple[BrokerPosition, ...]:
    positions: tuple[BrokerPosition, ...] = await broker.positions()  # type: ignore[attr-defined]
    return positions


__all__ = ["Divergence", "Reconciler", "Reconciliation", "reconcile_state"]
