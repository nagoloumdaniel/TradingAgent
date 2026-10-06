"""Halt and resume commands (F-019, RM-015, TASK-036).

The state of a scope is its latest command. Reading fails closed: if the state cannot be
read, trading is halted. Closing positions is never implied: only a halt command that
asks for it explicitly carries it.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, Select, func, insert, select
from sqlalchemy.orm import Session

from tradingagent.core.halt import (
    GLOBAL,
    MARKET_PREFIX,
    PAIR_PREFIX,
    TRADING_SCOPES,
    HaltStatus,
    parse_pair_scope,
)
from tradingagent.core.states import HaltAction, HaltSource
from tradingagent.storage.models import HaltCommandRow

log = logging.getLogger(__name__)


class OperatorRequiredError(Exception):
    """Only an operator may lift a global halt (RM-007, RM-014, RM-017)."""


@dataclass(frozen=True)
class HaltCommand:
    scope: str
    action: HaltAction
    source: HaltSource
    reason: str
    actor: str
    occurred_at: datetime
    close_positions: bool = False


class HaltStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def issue(self, command: HaltCommand) -> int:
        if not command.reason.strip():
            raise ValueError("a halt or resume command needs a reason")
        if command.close_positions and command.action is not HaltAction.HALT:
            raise ValueError("only a halt can ask for open positions to be closed")
        if (
            command.scope == GLOBAL
            and command.action is HaltAction.RESUME
            and command.source is HaltSource.AUTOMATIC
        ):
            raise OperatorRequiredError("a global halt can only be lifted by an operator")
        with self._engine.begin() as connection:
            command_id = connection.execute(
                insert(HaltCommandRow)
                .values(
                    scope=command.scope,
                    action=command.action,
                    close_positions=command.close_positions,
                    source=command.source,
                    reason=command.reason,
                    actor=command.actor,
                    occurred_at=command.occurred_at,
                )
                .returning(HaltCommandRow.id)
            ).scalar_one()
        log.warning("%s %s by %s: %s", command.scope, command.action, command.actor, command.reason)
        return int(command_id)

    def status(self, scopes: Iterable[str] = TRADING_SCOPES) -> HaltStatus:
        """Halted if any scope is halted. Unreadable state counts as halted."""
        try:
            latest = self._latest(tuple(scopes))
        except Exception as error:
            log.exception("halt state unreadable")
            return HaltStatus(True, False, (f"halt state unreadable, refusing: {error!r}",))
        active = [row for row in latest if row.action is not HaltAction.RESUME]
        return HaltStatus(
            halted=bool(active),
            close_positions=any(row.close_positions for row in active),
            reasons=tuple(
                f"{row.scope}: {row.reason} ({row.source}, {row.actor}, "
                f"{row.occurred_at.isoformat()})"
                for row in active
            ),
        )

    def is_halted(self, scope: str) -> bool:
        return self.status((scope,)).halted

    def halted_pairs(self) -> set[tuple[str, str]]:
        """Quarantined (strategy, market) pairs."""
        newest = (
            select(func.max(HaltCommandRow.id))
            .where(HaltCommandRow.scope.startswith(PAIR_PREFIX))
            .group_by(HaltCommandRow.scope)
        )
        latest = self._rows(select(HaltCommandRow).where(HaltCommandRow.id.in_(newest)))
        return {parse_pair_scope(row.scope) for row in latest if row.action is HaltAction.HALT}

    def halted_markets(self) -> set[str]:
        """Markets disabled by the operator (TASK-023)."""
        newest = (
            select(func.max(HaltCommandRow.id))
            .where(HaltCommandRow.scope.startswith(MARKET_PREFIX))
            .group_by(HaltCommandRow.scope)
        )
        latest = self._rows(select(HaltCommandRow).where(HaltCommandRow.id.in_(newest)))
        return {
            row.scope.removeprefix(MARKET_PREFIX) for row in latest if row.action is HaltAction.HALT
        }

    def history(self, scope: str, limit: int = 20) -> list[HaltCommandRow]:
        return self._rows(
            select(HaltCommandRow)
            .where(HaltCommandRow.scope == scope)
            .order_by(HaltCommandRow.id.desc())
            .limit(limit)
        )

    def _latest(self, scopes: tuple[str, ...]) -> list[HaltCommandRow]:
        newest = (
            select(func.max(HaltCommandRow.id))
            .where(HaltCommandRow.scope.in_(scopes))
            .group_by(HaltCommandRow.scope)
        )
        return self._rows(select(HaltCommandRow).where(HaltCommandRow.id.in_(newest)))

    def _rows(self, query: Select[HaltCommandRow]) -> list[HaltCommandRow]:
        with Session(self._engine) as session:
            return list(session.scalars(query).all())
