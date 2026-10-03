"""Database schema, section 11 of the specification.

Append-only tables (signal_events, executions, trades, audit_log) are locked by triggers
created in the migrations, so they can only be relied on in a migrated database.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    BigInteger,
    Enum,
    ForeignKey,
    Index,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from tradingagent.core.market import Direction
from tradingagent.core.mode import AiFilter, TradingMode
from tradingagent.core.states import OrderState, PositionState, RiskOutcome, Severity, SignalState
from tradingagent.core.timeframe import Timeframe
from tradingagent.storage.types import ExactDecimal, UtcDateTime

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def enum_type(enum_class: type[StrEnum]) -> Enum:
    """Stored as its value, with a CHECK constraint so the database rejects unknown values."""
    return Enum(
        enum_class,
        name=enum_class.__name__.lower(),
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: UtcDateTime(),
        Decimal: ExactDecimal(),
    }


class CandleRow(Base):
    __tablename__ = "candles"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "open_time"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[Timeframe] = mapped_column(enum_type(Timeframe))
    open_time: Mapped[datetime]
    open: Mapped[float]
    high: Mapped[float]
    low: Mapped[float]
    close: Mapped[float]
    source: Mapped[str] = mapped_column(String(32))
    ingested_at: Mapped[datetime]


class StrategyVersionRow(Base):
    """Snapshot of a manifest the first time it is used, so old trades stay explainable."""

    __tablename__ = "strategy_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    ref: Mapped[str] = mapped_column(String(80), unique=True)
    strategy_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(32))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    first_seen_at: Mapped[datetime]

    signals: Mapped[list["SignalRow"]] = relationship(back_populates="strategy_version")


class SignalRow(Base):
    __tablename__ = "signals"
    __table_args__ = (Index("ix_signals_symbol_generated_at", "symbol", "generated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    strategy_version_id: Mapped[int] = mapped_column(ForeignKey("strategy_versions.id"))
    symbol: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[Timeframe] = mapped_column(enum_type(Timeframe))
    direction: Mapped[Direction] = mapped_column(enum_type(Direction))
    mode: Mapped[TradingMode] = mapped_column(enum_type(TradingMode))
    observed_price: Mapped[float]
    entry_low: Mapped[float]
    entry_high: Mapped[float]
    stop_loss: Mapped[float]
    take_profits: Mapped[list[float]] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    indicators: Mapped[dict[str, float]] = mapped_column(JSON)
    generated_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    state: Mapped[SignalState] = mapped_column(enum_type(SignalState))

    strategy_version: Mapped[StrategyVersionRow] = relationship(back_populates="signals")
    events: Mapped[list["SignalEventRow"]] = relationship(back_populates="signal")
    orders: Mapped[list["OrderRow"]] = relationship(back_populates="signal")


class SignalEventRow(Base):
    """Append-only history of signal state changes."""

    __tablename__ = "signal_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    state: Mapped[SignalState] = mapped_column(enum_type(SignalState))
    occurred_at: Mapped[datetime]
    detail: Mapped[str | None] = mapped_column(Text)

    signal: Mapped[SignalRow] = relationship(back_populates="events")


class AiCallRow(Base):
    __tablename__ = "ai_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    ai_filter: Mapped[AiFilter | None] = mapped_column(enum_type(AiFilter))
    request: Mapped[dict[str, Any]] = mapped_column(JSON)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    verdict: Mapped[str | None] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None]
    cost_eur: Mapped[Decimal | None]
    called_at: Mapped[datetime]


class RiskDecisionRow(Base):
    __tablename__ = "risk_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    outcome: Mapped[RiskOutcome] = mapped_column(enum_type(RiskOutcome))
    reason: Mapped[str] = mapped_column(Text)
    checks: Mapped[dict[str, Any]] = mapped_column(JSON)
    volume: Mapped[Decimal | None]
    risk_eur: Mapped[Decimal | None]
    margin_eur: Mapped[Decimal | None]
    decided_at: Mapped[datetime]


class OrderRow(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    direction: Mapped[Direction] = mapped_column(enum_type(Direction))
    volume: Mapped[Decimal]
    requested_price: Mapped[float]
    stop_loss: Mapped[float]
    take_profit: Mapped[float | None]
    mode: Mapped[TradingMode] = mapped_column(enum_type(TradingMode))
    state: Mapped[OrderState] = mapped_column(enum_type(OrderState))
    broker_order_ticket: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    retcode: Mapped[int | None]
    broker_comment: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    signal: Mapped[SignalRow] = relationship(back_populates="orders")
    executions: Mapped[list["ExecutionRow"]] = relationship(back_populates="order")


class ExecutionRow(Base):
    """Append-only fills reported by the broker."""

    __tablename__ = "executions"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    broker_deal_ticket: Mapped[int] = mapped_column(BigInteger, unique=True)
    price: Mapped[float]
    volume: Mapped[Decimal]
    slippage: Mapped[float | None]
    executed_at: Mapped[datetime]

    order: Mapped[OrderRow] = relationship(back_populates="executions")


class PositionRow(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_position_ticket: Mapped[int] = mapped_column(BigInteger, unique=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    direction: Mapped[Direction] = mapped_column(enum_type(Direction))
    volume: Mapped[Decimal]
    open_price: Mapped[float]
    stop_loss: Mapped[float]
    take_profit: Mapped[float | None]
    mode: Mapped[TradingMode] = mapped_column(enum_type(TradingMode))
    state: Mapped[PositionState] = mapped_column(enum_type(PositionState))
    opened_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    order: Mapped[OrderRow] = relationship()
    trade: Mapped["TradeRow | None"] = relationship(back_populates="position")


class TradeRow(Base):
    """Append-only closed trades, the base of every performance figure."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), unique=True)
    mode: Mapped[TradingMode] = mapped_column(enum_type(TradingMode))
    closed_at: Mapped[datetime] = mapped_column(index=True)
    close_price: Mapped[float]
    pnl_eur: Mapped[Decimal]
    risk_eur: Mapped[Decimal]
    exit_reason: Mapped[str] = mapped_column(String(32))

    position: Mapped[PositionRow] = relationship(back_populates="trade")


class ReportRow(Base):
    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("period", "window_start"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    period: Mapped[str] = mapped_column(String(16))
    window_start: Mapped[datetime]
    window_end: Mapped[datetime]
    content: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime]
    sent_at: Mapped[datetime | None]


class SystemEventRow(Base):
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    severity: Mapped[Severity] = mapped_column(enum_type(Severity))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(index=True)


class AuditLogRow(Base):
    """Append-only record of operator commands and decisions."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(index=True)
