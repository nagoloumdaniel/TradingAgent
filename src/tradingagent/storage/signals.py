"""Signal persistence (F-009, F-012, RM-009, TASK-034).

The idempotency key is unique in the database: two evaluations of the same candle, racing
or separated by a restart, can only ever store one signal. The signal, the strategy version
it came from and its first lifecycle event are written in one transaction, so a crash never
leaves a signal without its history.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Connection, Engine, insert, select

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import Severity, SignalState
from tradingagent.core.timeframe import Timeframe
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

    def record_system_event(
        self, kind: str, severity: Severity, detail: Mapping[str, Any], at: datetime
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(SystemEventRow).values(
                    kind=kind, severity=severity, detail=dict(detail), occurred_at=at
                )
            )
