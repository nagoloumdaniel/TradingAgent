"""Signal persistence (F-009, F-012, RM-009, TASK-034).

The idempotency key is unique in the database: two evaluations of the same candle, racing
or separated by a restart, can only ever store one signal. The signal, the strategy version
it came from and its first lifecycle event are written in one transaction, so a crash never
leaves a signal without its history.
"""

import hashlib
import json
import statistics
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, Engine, insert, select
from sqlalchemy.orm import Session

from tradingagent.core.market import Direction
from tradingagent.core.mode import AiFilter, TradingMode
from tradingagent.core.states import Severity, SignalState
from tradingagent.core.timeframe import Timeframe
from tradingagent.signals.lifecycle import validate_transition
from tradingagent.storage._conflicts import insert_ignoring_duplicates
from tradingagent.storage.models import (
    SignalEventRow,
    SignalRow,
    StrategyVersionRow,
    SystemEventRow,
)
from tradingagent.strategies.manifest import StrategyManifest


class ManifestChangedError(Exception):
    """A manifest changed under an already used reference: old signals would lie."""


def idempotency_key(ref: str, symbol: str, timeframe: Timeframe, candle_close: datetime) -> str:
    """Strategy version, market, timeframe and triggering candle: one signal at most per key."""
    if candle_close.utcoffset() != timedelta(0):
        raise ValueError(f"candle_close must be UTC, got {candle_close!r}")
    return f"{ref}:{symbol}:{timeframe}:{candle_close.strftime('%Y-%m-%dT%H:%MZ')}"


@dataclass(frozen=True)
class SignalRecord:
    idempotency_key: str
    manifest: StrategyManifest
    symbol: str
    timeframe: Timeframe
    direction: Direction
    mode: TradingMode
    observed_price: float
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profits: tuple[float, ...]
    reason: str
    indicators: Mapping[str, float]
    generated_at: datetime
    expires_at: datetime


class SignalRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(self, signal: SignalRecord) -> int | None:
        """Store a new signal and return its id, or None if this key was already stored."""
        with self._engine.begin() as connection:
            version_id = self._strategy_version_id(connection, signal.manifest, signal.generated_at)
            statement = insert_ignoring_duplicates(
                self._engine, SignalRow, ("idempotency_key",)
            ).values(
                idempotency_key=signal.idempotency_key,
                strategy_version_id=version_id,
                symbol=signal.symbol,
                timeframe=signal.timeframe,
                direction=signal.direction,
                mode=signal.mode,
                observed_price=signal.observed_price,
                entry_low=signal.entry_low,
                entry_high=signal.entry_high,
                stop_loss=signal.stop_loss,
                take_profits=list(signal.take_profits),
                reason=signal.reason,
                indicators=dict(signal.indicators),
                generated_at=signal.generated_at,
                expires_at=signal.expires_at,
                state=SignalState.CANDIDATE,
            )
            signal_id = connection.execute(statement.returning(SignalRow.id)).scalar_one_or_none()
            if signal_id is None:
                return None
            self._insert_first_event(connection, signal_id, signal.generated_at)
            return signal_id

    def _strategy_version_id(
        self, connection: Connection, manifest: StrategyManifest, seen_at: datetime
    ) -> int:
        snapshot = manifest.model_dump(mode="json")
        content_hash = hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        connection.execute(
            insert_ignoring_duplicates(self._engine, StrategyVersionRow, ("ref",)).values(
                ref=manifest.ref,
                strategy_id=manifest.strategy_id,
                version=manifest.version,
                manifest=snapshot,
                content_hash=content_hash,
                first_seen_at=seen_at,
            )
        )
        version_id, stored_hash = connection.execute(
            select(StrategyVersionRow.id, StrategyVersionRow.content_hash).where(
                StrategyVersionRow.ref == manifest.ref
            )
        ).one()
        if stored_hash != content_hash:
            raise ManifestChangedError(
                f"{manifest.ref} differs from the manifest first used under that reference: "
                "bump its version"
            )
        return int(version_id)

    def _insert_first_event(self, connection: Connection, signal_id: int, at: datetime) -> None:
        connection.execute(
            insert(SignalEventRow).values(
                signal_id=signal_id, state=SignalState.CANDIDATE, occurred_at=at, detail=None
            )
        )

    def typical_stop_distance(self, ref: str, symbol: str, recent: int = 20) -> Decimal | None:
        """Median stop distance of the strategy's last signals on this market (RM-019 at
        start-up), from the middle of the entry zone. None without any signal yet."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(SignalRow.entry_low, SignalRow.entry_high, SignalRow.stop_loss)
                .join(StrategyVersionRow)
                .where(StrategyVersionRow.ref == ref, SignalRow.symbol == symbol)
                .order_by(SignalRow.generated_at.desc())
                .limit(recent)
            ).all()
        if not rows:
            return None
        distances = [
            abs((Decimal(str(low)) + Decimal(str(high))) / 2 - Decimal(str(stop)))
            for low, high, stop in rows
        ]
        return Decimal(str(statistics.median(distances)))

    def record_system_event(
        self, kind: str, severity: Severity, detail: Mapping[str, Any], at: datetime
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(SystemEventRow).values(
                    kind=kind, severity=severity, detail=dict(detail), occurred_at=at
                )
            )


@dataclass(frozen=True)
class RecentSignal:
    """One row of the operator's /signals listing (TASK-022)."""

    idempotency_key: str
    symbol: str
    timeframe: Timeframe
    direction: Direction
    mode: TradingMode
    state: SignalState
    generated_at: datetime


def read_recent_signals(engine: Engine, limit: int = 10) -> list[RecentSignal]:
    """The most recent signals, newest first. Read-only, used by the Telegram listing."""
    statement = select(SignalRow).order_by(SignalRow.generated_at.desc()).limit(limit)
    with Session(engine) as session:
        rows = session.scalars(statement).all()
    return [
        RecentSignal(
            idempotency_key=row.idempotency_key,
            symbol=row.symbol,
            timeframe=row.timeframe,
            direction=row.direction,
            mode=row.mode,
            state=row.state,
            generated_at=row.generated_at,
        )
        for row in rows
    ]


def transition(
    engine: Engine,
    signal_id: int,
    target: SignalState,
    occurred_at: datetime,
    detail: str | None = None,
) -> int:
    """Apply one RM-018 transition and persist its event in the same transaction.

    The row is locked for the duration of the transaction: two racing transitions are
    serialized, so the second one is validated against the already-moved state and
    refused when it is no longer legal. On SQLite the single-writer core gives the same
    guarantee.
    """
    with Session(engine) as session:
        signal = session.scalars(
            select(SignalRow).where(SignalRow.id == signal_id).with_for_update()
        ).first()
        if signal is None:
            raise ValueError(f"unknown signal {signal_id}")
        validate_transition(signal.state, target)
        signal.state = target
        event = SignalEventRow(
            signal_id=signal_id, state=target, occurred_at=occurred_at, detail=detail
        )
        session.add(event)
        session.commit()
        return int(event.id)


def history(engine: Engine, signal_id: int) -> list[SignalEventRow]:
    """The signal's full lifecycle, oldest first — nothing can be lost or rewritten."""
    statement = (
        select(SignalEventRow)
        .where(SignalEventRow.signal_id == signal_id)
        .order_by(SignalEventRow.id)
    )
    with Session(engine) as session:
        return list(session.scalars(statement).all())


@dataclass(frozen=True)
class SignalDetail:
    """Everything the agent loop needs to decide, notify and execute one signal."""

    id: int
    idempotency_key: str
    strategy_ref: str
    symbol: str
    timeframe: Timeframe
    direction: Direction
    mode: TradingMode
    observed_price: float
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profits: tuple[float, ...]
    reason: str
    indicators: dict[str, float]
    generated_at: datetime
    expires_at: datetime
    state: SignalState
    ai_filter: AiFilter = AiFilter.SHADOW


def get_signal(engine: Engine, signal_id: int) -> SignalDetail | None:
    """One signal with its strategy reference, or None when the id is unknown."""
    statement = (
        select(SignalRow, StrategyVersionRow.ref, StrategyVersionRow.manifest)
        .join(StrategyVersionRow, SignalRow.strategy_version_id == StrategyVersionRow.id)
        .where(SignalRow.id == signal_id)
    )
    with Session(engine) as session:
        row = session.execute(statement).first()
    if row is None:
        return None
    signal, ref, manifest = row
    return SignalDetail(
        id=int(signal.id),
        idempotency_key=signal.idempotency_key,
        strategy_ref=str(ref),
        symbol=signal.symbol,
        timeframe=signal.timeframe,
        direction=signal.direction,
        mode=signal.mode,
        observed_price=signal.observed_price,
        entry_low=signal.entry_low,
        entry_high=signal.entry_high,
        stop_loss=signal.stop_loss,
        take_profits=tuple(signal.take_profits),
        reason=signal.reason,
        indicators=dict(signal.indicators),
        generated_at=signal.generated_at,
        expires_at=signal.expires_at,
        state=signal.state,
        ai_filter=_manifest_filter(manifest),
    )


def _manifest_filter(manifest: dict[str, Any]) -> AiFilter:
    try:
        return AiFilter(str(manifest.get("ai_filter", AiFilter.SHADOW)))
    except ValueError:
        return AiFilter.SHADOW


def pending_notifications(engine: Engine, limit: int = 50) -> list[int]:
    """Signals validated by risk but whose message was never delivered (F-013 retry).

    Their state stays VALIDATED: the Telegram outage must cost a delay, not the signal.
    """
    statement = (
        select(SignalRow.id)
        .where(SignalRow.state == SignalState.VALIDATED)
        .order_by(SignalRow.generated_at)
        .limit(limit)
    )
    with Session(engine) as session:
        return [int(value) for value in session.scalars(statement).all()]
