"""The versioned strategy registry (cahier v3 §14, §30, §35, §49).

One row per `(market, ref)` in `strategy_registry`. A strategy starts `DISCOVERED`, climbs
the lifecycle through the transitions allowed by `core.states.ALLOWED_STATUS_TRANSITIONS`,
and only a `LIVE` version may trade. A `LIVE` version is immutable: its parameters and its
identity are frozen, any change is published under a new `ref` with a `parent_ref` pointing
at the version it replaces.

Promotion is gated by the nine stages of §49, read from `validation_runs`. Every decision is
persisted with its reason, refusals included:

* the row itself carries `promotion_reason`, `promoted_at` and `updated_at`;
* every registration and every transition also appends a row to `audit_log`, the append-only
  operator journal. The row is written in the *same transaction* as the state change, so a
  transition can never exist without its actor and its reason. That is why this module writes
  `AuditLogRow` directly instead of calling `storage.audit.AuditStore`, which opens its own
  transaction: the audit table is shared, the transaction is not.

Nothing here reads the wall clock: the constructor takes a `clock`, and every mutating call
takes an explicit UTC timestamp. A naive datetime is refused.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from tradingagent.core.states import (
    ALLOWED_STATUS_TRANSITIONS,
    StrategyStatus,
    ValidationStage,
)
from tradingagent.registry.gates import missing_gates
from tradingagent.storage.models import (
    AuditLogRow,
    BacktestRunRow,
    StrategyRegistryRow,
    ValidationRunRow,
)

#: Where a version comes from. The AI may propose, only validation puts a version in production.
ORIGINS = frozenset({"human", "ai", "research"})

REGISTERED_ACTION = "strategy.registered"
TRANSITION_ACTION = "strategy.transition"


class RegistryError(Exception):
    """The registry cannot do what was asked."""


class DuplicateStrategyRef(RegistryError):
    """This (market, ref) is already registered: a ref is created once."""


class UnknownStrategyRef(RegistryError):
    """No entry for this (market, ref), or for the parent that was referenced."""


class IllegalStrategyTransition(RegistryError):
    """The target status is not reachable from the current one."""


class ImmutableLiveStrategy(RegistryError):
    """A LIVE version is frozen: any change produces a new ref."""


class MissingValidationGates(RegistryError):
    """Promotion asked before all nine gates of §49 were cleared."""


class MarketAlreadyLive(RegistryError):
    """A market has one LIVE strategy at a time; the current one must be deprecated first."""


@dataclass(frozen=True)
class StrategyHistory:
    """Everything a ref went through: its validations, its backtests and its life events."""

    market: str
    ref: str
    status: StrategyStatus
    updated_at: datetime
    validations: tuple[ValidationRunRow, ...]
    backtests: tuple[BacktestRunRow, ...]
    transitions: tuple[AuditLogRow, ...]


def parse_ref(ref: str) -> tuple[str, str]:
    """Split `id@version` into its parts. The version is what makes a ref immutable."""
    strategy_id, separator, version = ref.rpartition("@")
    if not separator or not strategy_id.strip() or not version.strip():
        raise ValueError(f"a strategy ref must be written id@version, got {ref!r}")
    return strategy_id.strip(), version.strip()


def _require_utc(value: datetime, field: str) -> datetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be UTC")
    return value


class StrategyRegistry:
    """Reads and writes the production registry. One instance per engine, cheap to rebuild."""

    def __init__(self, engine: Engine, clock: Callable[[], datetime] | None = None) -> None:
        self._engine = engine
        self._clock = clock or (lambda: datetime.now(UTC))

    # -----------------------------------------------------------------------------------
    # Registration and reads
    # -----------------------------------------------------------------------------------

    def register(
        self,
        market: str,
        ref: str,
        origin: str,
        parameters: Mapping[str, Any],
        parent_ref: str | None = None,
        dataset_fingerprint: str | None = None,
    ) -> int:
        """Create a version in `DISCOVERED` and return its row id.

        Refused when the `(market, ref)` pair already exists, when `parent_ref` names no
        version of the same market, or when the ref is not written `id@version`.
        """
        market = market.strip()
        if not market:
            raise ValueError("a strategy needs a market")
        strategy_id, version = parse_ref(ref)
        origin = origin.strip()
        if origin not in ORIGINS:
            raise ValueError(f"origin must be one of {', '.join(sorted(ORIGINS))}, got {origin!r}")
        if parent_ref is not None:
            self.get(market, parent_ref)  # raises UnknownStrategyRef, same market lineage

        created_at = _require_utc(self._clock(), "created_at")
        with self._engine.begin() as connection:
            self._require_absent(connection, market, ref)
            row_id = connection.execute(
                insert(StrategyRegistryRow)
                .values(
                    market=market,
                    ref=ref,
                    strategy_id=strategy_id,
                    version=version,
                    status=StrategyStatus.DISCOVERED,
                    parent_ref=parent_ref,
                    origin=origin,
                    parameters=dict(parameters),
                    results=None,
                    dataset_fingerprint=dataset_fingerprint,
                    promotion_reason=None,
                    created_at=created_at,
                    promoted_at=None,
                    updated_at=created_at,
                )
                .returning(StrategyRegistryRow.id)
            ).scalar_one()
            self._audit(
                connection,
                REGISTERED_ACTION,
                {
                    "market": market,
                    "ref": ref,
                    "origin": origin,
                    "parent_ref": parent_ref,
                },
                created_at,
                actor=origin,
            )
        return int(row_id)

    def get(self, market: str, ref: str) -> StrategyRegistryRow:
        with Session(self._engine) as session:
            row = session.scalar(
                select(StrategyRegistryRow).where(
                    StrategyRegistryRow.market == market.strip(),
                    StrategyRegistryRow.ref == ref,
                )
            )
        if row is None:
            raise UnknownStrategyRef(f"no strategy {ref!r} on {market!r}")
        return row

    def list_market(self, market: str) -> list[StrategyRegistryRow]:
        with Session(self._engine) as session:
            return list(
                session.scalars(
                    select(StrategyRegistryRow)
                    .where(StrategyRegistryRow.market == market.strip())
                    .order_by(StrategyRegistryRow.id)
                ).all()
            )

    def markets(self) -> list[str]:
        """Every market that has at least one registered version, alphabetically."""
        with Session(self._engine) as session:
            return list(
                session.scalars(
                    select(StrategyRegistryRow.market)
                    .distinct()
                    .order_by(StrategyRegistryRow.market)
                ).all()
            )

    def active(self, market: str) -> str | None:
        """The ref that may trade on this market, or None when nothing is LIVE."""
        with Session(self._engine) as session:
            refs = list(
                session.scalars(
                    select(StrategyRegistryRow.ref)
                    .where(
                        StrategyRegistryRow.market == market.strip(),
                        StrategyRegistryRow.status == StrategyStatus.LIVE,
                    )
                    .order_by(StrategyRegistryRow.id)
                ).all()
            )
        return refs[-1] if refs else None

    def passed_stages(self, ref: str, market: str) -> frozenset[ValidationStage]:
        """The gates currently cleared: a later failure revokes an earlier pass."""
        latest: dict[ValidationStage, bool] = {}
        for run in self._validations(ref, market):
            latest[run.stage] = run.passed
        return frozenset(stage for stage, passed in latest.items() if passed)

    def history(self, ref: str, market: str) -> StrategyHistory:
        row = self.get(market, ref)
        return StrategyHistory(
            market=row.market,
            ref=row.ref,
            status=row.status,
            updated_at=row.updated_at,
            validations=tuple(self._validations(ref, market)),
            backtests=tuple(self._backtests(ref, market)),
            transitions=tuple(self._life(market, ref)),
        )

    # -----------------------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------------------

    def transition(
        self,
        market: str,
        ref: str,
        target: StrategyStatus,
        actor: str,
        reason: str,
        at: datetime,
    ) -> None:
        """Move a version to `target`, or refuse.

        Only the transitions of `ALLOWED_STATUS_TRANSITIONS` are accepted. Reaching `LIVE`
        also requires the nine gates of §49 and that no other version of the market is LIVE.
        """
        actor = actor.strip()
        reason = reason.strip()
        if not actor:
            raise ValueError("a transition needs an actor")
        if not reason:
            raise ValueError("a transition needs a reason")
        _require_utc(at, "at")
        target = StrategyStatus(target)
        row = self.get(market, ref)
        allowed = ALLOWED_STATUS_TRANSITIONS[row.status]
        if target not in allowed:
            listing = ", ".join(sorted(status.value for status in allowed)) or "none"
            raise IllegalStrategyTransition(
                f"illegal strategy transition {row.status.value} -> {target.value} for "
                f"{ref} on {row.market}; allowed from {row.status.value}: {listing}"
            )
        if target is StrategyStatus.LIVE:
            self._require_gates(row.market, ref)
            self._require_no_other_live(row.market, ref)

        values: dict[str, Any] = {"status": target, "updated_at": at}
        if target is StrategyStatus.LIVE:
            values["promoted_at"] = at
            values["promotion_reason"] = reason
        with self._engine.begin() as connection:
            connection.execute(
                update(StrategyRegistryRow)
                .where(
                    StrategyRegistryRow.market == row.market,
                    StrategyRegistryRow.ref == ref,
                )
                .values(**values)
            )
            self._audit(
                connection,
                TRANSITION_ACTION,
                {
                    "market": row.market,
                    "ref": ref,
                    "from": row.status.value,
                    "to": target.value,
                    "reason": reason,
                },
                at,
                actor=actor,
            )

    def promote(
        self,
        market: str,
        ref: str,
        actor: str,
        reason: str,
        at: datetime,
    ) -> None:
        """Send a candidate to production, once the nine gates of §49 are cleared.

        Refuses while gates are missing, naming them all, and refuses to touch a LIVE
        version: a LIVE version is immutable, a change means a new ref.
        """
        row = self.get(market, ref)
        if row.status is StrategyStatus.LIVE:
            raise ImmutableLiveStrategy(
                f"{ref} is already LIVE on {row.market}: a LIVE version is immutable, "
                "any change must be published under a new ref"
            )
        missing = missing_gates(self.passed_stages(ref, row.market))
        if missing:
            names = ", ".join(stage.value for stage in missing)
            raise MissingValidationGates(
                f"cannot promote {ref} on {row.market}: {len(missing)} gate(s) of "
                f"section 49 missing: {names}"
            )
        self.transition(row.market, ref, StrategyStatus.LIVE, actor, reason, at)

    # -----------------------------------------------------------------------------------
    # Evidence
    # -----------------------------------------------------------------------------------

    def record_validation(
        self,
        ref: str,
        market: str,
        stage: ValidationStage,
        passed: bool,
        detail: Mapping[str, Any],
        at: datetime,
    ) -> int:
        """Append one gate verdict. A failed run never opens the gate."""
        _require_utc(at, "at")
        stage = ValidationStage(stage)
        self.get(market, ref)
        with self._engine.begin() as connection:
            run_id = connection.execute(
                insert(ValidationRunRow)
                .values(
                    ref=ref,
                    market=market.strip(),
                    stage=stage,
                    passed=bool(passed),
                    detail=dict(detail),
                    created_at=at,
                )
                .returning(ValidationRunRow.id)
            ).scalar_one()
        return int(run_id)

    def record_backtest(
        self,
        ref: str,
        market: str,
        *,
        dataset_id: str,
        fingerprint: str,
        window_start: datetime,
        window_end: datetime,
        metrics: Mapping[str, Any],
        costs: Mapping[str, Any],
        report_path: str | None = None,
        at: datetime | None = None,
    ) -> int:
        """Append one reproducible backtest: its dataset, its window and its numbers."""
        created_at = _require_utc(at or self._clock(), "at")
        _require_utc(window_start, "window_start")
        _require_utc(window_end, "window_end")
        self.get(market, ref)
        with self._engine.begin() as connection:
            run_id = connection.execute(
                insert(BacktestRunRow)
                .values(
                    ref=ref,
                    market=market.strip(),
                    dataset_id=dataset_id,
                    fingerprint=fingerprint,
                    window_start=window_start,
                    window_end=window_end,
                    metrics=dict(metrics),
                    costs=dict(costs),
                    report_path=report_path,
                    created_at=created_at,
                )
                .returning(BacktestRunRow.id)
            ).scalar_one()
        return int(run_id)

    # -----------------------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------------------

    def _require_absent(self, connection: Connection, market: str, ref: str) -> None:
        existing = connection.execute(
            select(StrategyRegistryRow.id).where(
                StrategyRegistryRow.market == market,
                StrategyRegistryRow.ref == ref,
            )
        ).first()
        if existing is not None:
            raise DuplicateStrategyRef(
                f"{ref} is already registered on {market}: a ref is created once"
            )

    def _require_gates(self, market: str, ref: str) -> None:
        missing = missing_gates(self.passed_stages(ref, market))
        if missing:
            names = ", ".join(stage.value for stage in missing)
            raise MissingValidationGates(
                f"cannot promote {ref} on {market}: {len(missing)} gate(s) of section 49 "
                f"missing: {names}"
            )

    def _require_no_other_live(self, market: str, ref: str) -> None:
        with Session(self._engine) as session:
            live = session.scalar(
                select(StrategyRegistryRow.ref)
                .where(
                    StrategyRegistryRow.market == market,
                    StrategyRegistryRow.status == StrategyStatus.LIVE,
                    StrategyRegistryRow.ref != ref,
                )
                .order_by(StrategyRegistryRow.id)
            )
        if live is not None:
            raise MarketAlreadyLive(
                f"{live} is already LIVE on {market}: deprecate it before promoting {ref}"
            )

    def _audit(
        self,
        connection: Connection,
        action: str,
        detail: Mapping[str, Any],
        at: datetime,
        *,
        actor: str,
    ) -> None:
        connection.execute(
            insert(AuditLogRow).values(
                actor=actor, action=action, detail=dict(detail), occurred_at=at
            )
        )

    def _validations(self, ref: str, market: str) -> Sequence[ValidationRunRow]:
        with Session(self._engine) as session:
            return list(
                session.scalars(
                    select(ValidationRunRow)
                    .where(ValidationRunRow.ref == ref, ValidationRunRow.market == market.strip())
                    .order_by(ValidationRunRow.id)
                ).all()
            )

    def _backtests(self, ref: str, market: str) -> Sequence[BacktestRunRow]:
        with Session(self._engine) as session:
            return list(
                session.scalars(
                    select(BacktestRunRow)
                    .where(BacktestRunRow.ref == ref, BacktestRunRow.market == market.strip())
                    .order_by(BacktestRunRow.id)
                ).all()
            )

    def _life(self, market: str, ref: str) -> Sequence[AuditLogRow]:
        """The life of a ref, read back from the audit journal (JSON detail filtered here)."""
        with Session(self._engine) as session:
            rows = list(
                session.scalars(
                    select(AuditLogRow)
                    .where(AuditLogRow.action.startswith("strategy."))
                    .order_by(AuditLogRow.id)
                ).all()
            )
        return [
            row
            for row in rows
            if row.detail.get("market") == market and row.detail.get("ref") == ref
        ]
