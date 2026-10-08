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
from tradingagent.core.states import (
    AnalysisKind,
    ExecutionEventKind,
    HaltAction,
    HaltSource,
    OrderState,
    PositionState,
    ProposalStatus,
    RiskOutcome,
    Severity,
    SignalState,
    StrategyStatus,
    ValidationStage,
)
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
    # The market the report is about, when the window holds exactly one. A report that
    # covers several markets has no single market: the column stays NULL, and the
    # `/reports` page shows it under "tous marchés" rather than under a lie.
    market: Mapped[str | None] = mapped_column(String(32), index=True)


class SystemEventRow(Base):
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    severity: Mapped[Severity] = mapped_column(enum_type(Severity))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(index=True)
    # The instrument the event belongs to, when the caller knows one. Account-wide events
    # (clock mismatch, live activation, mode command) have no symbol: NULL, never a guess.
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)


class AuditLogRow(Base):
    """Append-only record of operator commands and decisions."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(index=True)


class HaltCommandRow(Base):
    """Append-only halt and resume commands (F-019, RM-015). The current state of a scope
    is its latest command, so the full history of who stopped what, and why, is kept."""

    __tablename__ = "halt_commands"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(128), index=True)
    action: Mapped[HaltAction] = mapped_column(enum_type(HaltAction))
    close_positions: Mapped[bool]
    source: Mapped[HaltSource] = mapped_column(enum_type(HaltSource))
    reason: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime]


class AccountSnapshotRow(Base):
    """Periodic equity records: anchors of the reports' balances and equity curve."""

    __tablename__ = "account_snapshots"
    __table_args__ = (Index("ix_account_snapshots_at", "at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    equity: Mapped[Decimal]
    balance: Mapped[Decimal]
    at: Mapped[datetime]


# ---------------------------------------------------------------------------------------
# Cahier v3: strategy lifecycle, research evidence, AI proposals and execution telemetry.
# ---------------------------------------------------------------------------------------


class StrategyRegistryRow(Base):
    """The production registry (cahier v3 §14, §30).

    One row per (market, ref). The status is the deployment state; the identity of a LIVE
    version is immutable, so any change produces a new `ref` with its own row.
    """

    __tablename__ = "strategy_registry"
    __table_args__ = (
        UniqueConstraint("market", "ref"),
        Index("ix_strategy_registry_market_status", "market", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    market: Mapped[str] = mapped_column(String(32))
    ref: Mapped[str] = mapped_column(String(80))
    strategy_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(32))
    status: Mapped[StrategyStatus] = mapped_column(enum_type(StrategyStatus))
    parent_ref: Mapped[str | None] = mapped_column(String(80))
    origin: Mapped[str] = mapped_column(String(32))  # human | ai | research
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON)
    results: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    dataset_fingerprint: Mapped[str | None] = mapped_column(String(64))
    promotion_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime]
    promoted_at: Mapped[datetime | None]
    updated_at: Mapped[datetime]


class BacktestRunRow(Base):
    """One reproducible backtest: its dataset, its window and its measured numbers."""

    __tablename__ = "backtest_runs"
    __table_args__ = (Index("ix_backtest_runs_ref_market", "ref", "market"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ref: Mapped[str] = mapped_column(String(80))
    market: Mapped[str] = mapped_column(String(32))
    dataset_id: Mapped[str] = mapped_column(String(120))
    fingerprint: Mapped[str] = mapped_column(String(64))
    window_start: Mapped[datetime]
    window_end: Mapped[datetime]
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON)
    costs: Mapped[dict[str, Any]] = mapped_column(JSON)
    report_path: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime]


class ValidationRunRow(Base):
    """One gate of the validation protocol, with its verdict and its evidence."""

    __tablename__ = "validation_runs"
    __table_args__ = (Index("ix_validation_runs_ref_stage", "ref", "stage"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ref: Mapped[str] = mapped_column(String(80))
    market: Mapped[str] = mapped_column(String(32))
    stage: Mapped[ValidationStage] = mapped_column(enum_type(ValidationStage))
    passed: Mapped[bool]
    detail: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime]


class AiAnalysisRow(Base):
    """What the AI observed, never what it decided (cahier v3 §5, §15, §16, §39)."""

    __tablename__ = "ai_analyses"
    __table_args__ = (Index("ix_ai_analyses_kind_market", "kind", "market"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[AnalysisKind] = mapped_column(enum_type(AnalysisKind))
    market: Mapped[str] = mapped_column(String(32))
    ref: Mapped[str | None] = mapped_column(String(80))
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"))
    model: Mapped[str] = mapped_column(String(64))
    request: Mapped[dict[str, Any]] = mapped_column(JSON)
    response: Mapped[str | None] = mapped_column(Text)
    findings: Mapped[dict[str, Any]] = mapped_column(JSON)
    cost_eur: Mapped[Decimal | None]
    created_at: Mapped[datetime]


class AiProposalRow(Base):
    """A hypothesis the AI proposes; it becomes production only through validation."""

    __tablename__ = "ai_proposals"
    __table_args__ = (Index("ix_ai_proposals_status_market", "status", "market"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    market: Mapped[str] = mapped_column(String(32))
    ref: Mapped[str | None] = mapped_column(String(80))
    analysis_id: Mapped[int | None] = mapped_column(ForeignKey("ai_analyses.id"))
    hypothesis: Mapped[str] = mapped_column(Text)
    proposed_change: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[ProposalStatus] = mapped_column(enum_type(ProposalStatus))
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime]
    decided_at: Mapped[datetime | None]


class ExecutionEventRow(Base):
    """Append-only execution telemetry: every hop of the signal-to-fill path (§20, §47)."""

    __tablename__ = "execution_events"
    __table_args__ = (Index("ix_execution_events_symbol_time", "symbol", "occurred_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"))
    symbol: Mapped[str] = mapped_column(String(32))
    kind: Mapped[ExecutionEventKind] = mapped_column(enum_type(ExecutionEventKind))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON)
    occurred_at: Mapped[datetime]


class DailyPerformanceRow(Base):
    """Per day, per mode, per market and per strategy: the dashboard's fast path."""

    __tablename__ = "daily_performance"
    __table_args__ = (UniqueConstraint("day", "mode", "market", "ref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[datetime]
    mode: Mapped[TradingMode] = mapped_column(enum_type(TradingMode))
    market: Mapped[str] = mapped_column(String(32))
    ref: Mapped[str] = mapped_column(String(80))
    trades: Mapped[int]
    wins: Mapped[int]
    pnl: Mapped[Decimal]
    risk_eur: Mapped[Decimal]
    created_at: Mapped[datetime]
