"""Every read the dashboard performs, in one testable place (§23-§34, §45, §51).

Read-only by construction: this module issues SELECT statements only — no ``insert``, no
``update``, no ``delete``, no DDL, no writable connection — and reads the EA bridge's JSON
reports without ever writing them. Amounts are ``Decimal`` and every aggregation happens in
Python because SQLite stores them as text, so no SQL ``sum`` may touch them (TASK-005,
:mod:`tradingagent.storage.types`).

The performance figures are *not* computed here: they come from
:func:`tradingagent.analytics.compute_performance` and :func:`tradingagent.analytics.group`,
the same code path the reports and the backtests use (C-001). The dashboard re-derives
nothing (§34). The arithmetic that does live here is plain aggregation of stored columns
into a display total — exposure and counters — never an indicator.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Engine, String, and_, cast, desc, func, or_, select, text, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tradingagent.ai.daily import regime_of, session_of
from tradingagent.analytics import Axis, Performance, Trade, compute_performance, group
from tradingagent.analytics.scalping import (
    Bucket,
    CostSummary,
    by_duration,
    by_hour,
    by_session,
    by_size,
    by_spread,
    by_weekday,
    cost_summary,
)
from tradingagent.config._yaml import read_yaml
from tradingagent.config.agent import RiskConfig, RiskProfile
from tradingagent.config.errors import ConfigError
from tradingagent.core.halt import TRADING_SCOPES, HaltStatus
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import (
    ExecutionEventKind,
    HaltAction,
    HaltSource,
    PositionState,
    RiskOutcome,
    Severity,
    StrategyStatus,
    ValidationStage,
)
from tradingagent.core.timeframe import Timeframe
from tradingagent.ea.bridge import (
    DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
    EaEvent,
    EaReport,
    EaStatus,
    read_reports,
)
from tradingagent.ea.health import ea_health
from tradingagent.storage.account import AccountStore, RefusedRisk, ReportData, Snapshot
from tradingagent.storage.daily import DailyPerformance, DailyPerformanceStore
from tradingagent.storage.halts import HaltStore
from tradingagent.storage.models import (
    AccountSnapshotRow,
    AiAnalysisRow,
    AiProposalRow,
    BacktestRunRow,
    CandleRow,
    ExecutionEventRow,
    ExecutionRow,
    HaltCommandRow,
    OrderRow,
    PositionRow,
    ReportRow,
    RiskDecisionRow,
    SignalRow,
    StrategyRegistryRow,
    StrategyVersionRow,
    SystemEventRow,
    TradeRow,
    ValidationRunRow,
)
from tradingagent.storage.positions import OpenPosition, PositionReader
from tradingagent.storage.scalping import ExecutionCosts, execution_costs, size_of
from tradingagent.storage.telemetry import ExecutionEventStore, LatencyStats
from tradingagent.web import format as display
from tradingagent.web import paging
from tradingagent.web.paging import Page

log = logging.getLogger(__name__)

# The whole-history window: bounded and tz-aware. A reader never depends on the clock to
# decide what exists.
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
FOREVER = datetime(9999, 1, 1, tzinfo=UTC)
TOTAL = "TOTAL"
# The bridge's own tolerance before a Guardian counts as OFFLINE (tradingagent.ea.bridge).
_EA_TIMEOUT_SECONDS = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS


def day_start(at: datetime) -> datetime:
    return at.replace(hour=0, minute=0, second=0, microsecond=0)


def week_start(at: datetime) -> datetime:
    return day_start(at) - timedelta(days=at.weekday())


def month_start(at: datetime) -> datetime:
    return day_start(at).replace(day=1)


# ---------------------------------------------------------------------------------------
# Trades: the single source of every performance figure (models.py).
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TradeEntry:
    """A closed trade and the signal that produced it.

    ``Trade`` itself stays pure (it is the shared analytics record, with no row id); the
    signal id is what the detail view keys on, and a signal produces at most one order,
    hence at most one position and one trade in this model.
    """

    signal_id: int
    trade: Trade


def trade_entries(
    engine: Engine, start: datetime = EPOCH, end: datetime = FOREVER
) -> list[TradeEntry]:
    """Closed trades of ``[start, end)``, read by the reporting reader itself.

    Reusing :class:`ReportData` rather than re-joining the five tables here is the point:
    the dashboard, the report and the monthly comparison count the same rows.
    """
    return [
        TradeEntry(signal_id=signal_id, trade=trade)
        for signal_id, trade in ReportData(engine).trades_between(start, end)
    ]


def trades_between(engine: Engine, start: datetime, end: datetime) -> list[Trade]:
    return [entry.trade for entry in trade_entries(engine, start, end)]


def all_trades(engine: Engine) -> list[Trade]:
    """Every closed trade ever recorded, oldest first.

    A monitoring dashboard reads the whole history; ``trades.closed_at`` is indexed and the
    table stays small at this agent's cadence (a handful of trades a day).
    """
    return trades_between(engine, EPOCH, FOREVER)


def all_trade_entries(engine: Engine) -> list[TradeEntry]:
    return trade_entries(engine, EPOCH, FOREVER)


@dataclass(frozen=True)
class TradeFilters:
    """The three filters of the trades page; ``None`` means "every value"."""

    market: str | None = None
    strategy: str | None = None
    mode: TradingMode | None = None

    def matches(self, trade: Trade) -> bool:
        return (
            (self.market is None or trade.symbol == self.market)
            and (self.strategy is None or trade.strategy_ref == self.strategy)
            and (self.mode is None or trade.mode is self.mode)
        )

    def apply(self, trades: Sequence[Trade]) -> list[Trade]:
        return [trade for trade in trades if self.matches(trade)]

    def apply_entries(self, entries: Sequence[TradeEntry]) -> list[TradeEntry]:
        return [entry for entry in entries if self.matches(entry.trade)]


def selectable_markets(trades: Iterable[Trade]) -> list[str]:
    return sorted({trade.symbol for trade in trades})


def selectable_strategies(trades: Iterable[Trade]) -> list[str]:
    return sorted({trade.strategy_ref for trade in trades})


TRADE_PAGE_SIZE = paging.PAGE_SIZE
TRADE_SEARCH_FIELDS: tuple[str, ...] = (
    "symbole",
    "stratégie",
    "mode",
    "motif de sortie",
    "ticket",
)


@dataclass(frozen=True)
class TradeListing:
    """One page of closed trades, plus the figures the selection produces.

    ``page`` is the paginated listing — bounded in SQL, ten rows at a time. ``performance``
    covers the *whole* filtered selection, not the visible page: a KPI card that described
    ten rows out of 400 would be a lie. Amounts are stored as text under SQLite, so that
    aggregation happens in :mod:`tradingagent.analytics` after a SQL-side filter, never in
    the browser and never on the full table when a filter is active.
    """

    page: Page[TradeEntry]
    performance: Performance
    markets: tuple[str, ...]
    strategies: tuple[str, ...]


def _trade_statement(conditions: Sequence[Any]) -> Any:
    """The five-table join a closed trade needs, with the page's own filters applied."""
    return (
        select(TradeRow, PositionRow, SignalRow, StrategyVersionRow)
        .join(PositionRow, TradeRow.position_id == PositionRow.id)
        .join(OrderRow, PositionRow.order_id == OrderRow.id)
        .join(SignalRow, OrderRow.signal_id == SignalRow.id)
        .join(StrategyVersionRow, SignalRow.strategy_version_id == StrategyVersionRow.id)
        .where(*conditions)
    )


def _trade_entry(
    trade: TradeRow, position: PositionRow, signal: SignalRow, version: StrategyVersionRow
) -> TradeEntry:
    return TradeEntry(
        signal_id=int(signal.id),
        trade=Trade(
            symbol=position.symbol,
            strategy_ref=version.ref,
            direction=position.direction,
            timeframe=signal.timeframe,
            mode=trade.mode,
            opened_at=position.opened_at,
            closed_at=trade.closed_at,
            pnl_eur=trade.pnl_eur,
            risk_eur=trade.risk_eur,
        ),
    )


def _trade_conditions(
    *,
    market: str,
    strategy: str,
    mode: TradingMode | None,
    needle: str,
) -> list[Any]:
    """Every filter as a SQL condition, so the filtering never happens in memory."""
    conditions: list[Any] = []
    if market:
        conditions.append(PositionRow.symbol == market)
    if strategy:
        conditions.append(StrategyVersionRow.ref == strategy)
    if mode is not None:
        conditions.append(TradeRow.mode == mode)
    conditions.extend(
        paging.text_search(
            needle,
            (
                PositionRow.symbol,
                StrategyVersionRow.ref,
                TradeRow.exit_reason,
                TradeRow.mode,
                PositionRow.direction,
                PositionRow.broker_position_ticket,
                OrderRow.broker_order_ticket,
            ),
        )
    )
    return conditions


def trade_page(
    engine: Engine,
    *,
    market: str = paging.ALL_MARKETS,
    strategy: str = "",
    mode: TradingMode | None = None,
    query: str = "",
    page: int = 1,
    page_size: int = TRADE_PAGE_SIZE,
) -> TradeListing:
    """The closed trades, newest first, one page, filtered and bounded in the database."""
    needle = query.strip()
    size = max(1, page_size)
    conditions = _trade_conditions(
        market=market, strategy=strategy.strip(), mode=mode, needle=needle
    )
    count_statement = (
        select(func.count())
        .select_from(TradeRow)
        .join(PositionRow, TradeRow.position_id == PositionRow.id)
        .join(OrderRow, PositionRow.order_id == OrderRow.id)
        .join(SignalRow, OrderRow.signal_id == SignalRow.id)
        .join(StrategyVersionRow, SignalRow.strategy_version_id == StrategyVersionRow.id)
        .where(*conditions)
    )
    with Session(engine) as session:
        total = int(session.scalar(count_statement) or 0)
        current, pages = paging.page_bounds(total, page, size)
        rows = session.execute(
            _trade_statement(conditions)
            .order_by(desc(TradeRow.closed_at), desc(TradeRow.id))
            .limit(size)
            .offset((current - 1) * size)
        ).all()
        # The selection behind the KPI cards, filtered in SQL and aggregated by `analytics`.
        selected = session.execute(_trade_statement(conditions)).all()
    entries = [_trade_entry(*row) for row in rows]
    every = [_trade_entry(*row) for row in selected]
    return TradeListing(
        page=Page(
            rows=tuple(entries),
            total=total,
            page=current,
            pages=pages,
            page_size=size,
            query=needle,
            market=market,
            path="/trades",
            filters=(("strategy", strategy.strip()), ("mode", "" if mode is None else mode.value)),
            noun="trade",
        ),
        performance=compute_performance([entry.trade for entry in every]),
        # The selectors list what the database holds, never just what the current filter
        # returned: a selector that narrowed to its own choice would trap the operator.
        markets=available_markets(engine),
        strategies=traded_strategies(engine),
    )


def traded_strategies(engine: Engine) -> tuple[str, ...]:
    """Every strategy reference that has produced a trade, sorted, read with one DISTINCT."""
    statement = (
        select(StrategyVersionRow.ref)
        .distinct()
        .select_from(TradeRow)
        .join(PositionRow, TradeRow.position_id == PositionRow.id)
        .join(OrderRow, PositionRow.order_id == OrderRow.id)
        .join(SignalRow, OrderRow.signal_id == SignalRow.id)
        .join(StrategyVersionRow, SignalRow.strategy_version_id == StrategyVersionRow.id)
    )
    with Session(engine) as session:
        return tuple(sorted(str(ref) for ref in session.scalars(statement)))


@dataclass(frozen=True)
class ExecutionView:
    price: float
    volume: Decimal
    slippage: float | None
    executed_at: datetime
    broker_deal_ticket: int


@dataclass(frozen=True)
class TradeDetail:
    """One trade with the signal, the risk verdict and the fills that produced it."""

    signal_id: int
    trade: Trade
    close_price: float
    exit_reason: str
    signal_reason: str
    signal_state: str
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profits: tuple[float, ...]
    observed_price: float
    indicators: dict[str, float]
    risk_outcome: RiskOutcome | None
    risk_reason: str | None
    order_state: str | None
    broker_order_ticket: int | None
    executions: tuple[ExecutionView, ...]


def trade_detail(engine: Engine, signal_id: int) -> TradeDetail | None:
    """One trade and its provenance, keyed by its signal. ``None`` for an unknown id."""
    statement = (
        select(TradeRow, PositionRow, SignalRow, StrategyVersionRow, OrderRow)
        .join(PositionRow, TradeRow.position_id == PositionRow.id)
        .join(OrderRow, PositionRow.order_id == OrderRow.id)
        .join(SignalRow, OrderRow.signal_id == SignalRow.id)
        .join(StrategyVersionRow, SignalRow.strategy_version_id == StrategyVersionRow.id)
        .where(SignalRow.id == signal_id)
    )
    with Session(engine) as session:
        row = session.execute(statement).first()
        if row is None:
            return None
        trade_row, position, signal, version, order = row
        decision = session.scalars(
            select(RiskDecisionRow)
            .where(RiskDecisionRow.signal_id == signal.id)
            .order_by(desc(RiskDecisionRow.id))
            .limit(1)
        ).first()
        executions = session.scalars(
            select(ExecutionRow)
            .where(ExecutionRow.order_id == order.id)
            .order_by(ExecutionRow.executed_at)
        ).all()

    return TradeDetail(
        signal_id=int(signal.id),
        trade=Trade(
            symbol=position.symbol,
            strategy_ref=version.ref,
            direction=position.direction,
            timeframe=signal.timeframe,
            mode=trade_row.mode,
            opened_at=position.opened_at,
            closed_at=trade_row.closed_at,
            pnl_eur=trade_row.pnl_eur,
            risk_eur=trade_row.risk_eur,
        ),
        close_price=trade_row.close_price,
        exit_reason=trade_row.exit_reason,
        signal_reason=signal.reason,
        signal_state=str(signal.state),
        entry_low=signal.entry_low,
        entry_high=signal.entry_high,
        stop_loss=signal.stop_loss,
        take_profits=tuple(signal.take_profits),
        observed_price=signal.observed_price,
        indicators={key: float(value) for key, value in signal.indicators.items()},
        risk_outcome=decision.outcome if decision is not None else None,
        risk_reason=decision.reason if decision is not None else None,
        order_state=str(order.state),
        broker_order_ticket=order.broker_order_ticket,
        executions=tuple(
            ExecutionView(
                price=execution.price,
                volume=execution.volume,
                slippage=execution.slippage,
                executed_at=execution.executed_at,
                broker_deal_ticket=execution.broker_deal_ticket,
            )
            for execution in executions
        ),
    )


# ---------------------------------------------------------------------------------------
# Trade replay (§28): the whole provenance of one signal, in chronological order.
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalysisView:
    """What the AI observed. It never decided anything (§5, §15, §16, §39).

    Declared here because the replay page attaches an analysis to its signal; the AI
    laboratory page reuses the very same record.
    """

    id: int
    kind: str
    market: str
    ref: str | None
    model: str
    findings: dict[str, Any]
    response: str | None
    cost_eur: Decimal | None
    created_at: datetime


@dataclass(frozen=True)
class SignalView:
    """Every stored field of the signal, including the indicators that produced it."""

    id: int
    idempotency_key: str
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
    state: str


@dataclass(frozen=True)
class StrategyVersionView:
    """Which strategy version executed the trade (§27).

    The ``strategy_versions`` row is the immutable snapshot taken the first time the
    manifest was seen, so an old trade stays explainable even after the strategy moved on.
    The registry row adds the deployment state *today*; it may legitimately be absent.
    """

    ref: str
    strategy_id: str
    version: str
    content_hash: str
    manifest: dict[str, Any]
    first_seen_at: datetime
    status: StrategyStatus | None
    origin: str | None
    promoted_at: datetime | None
    promotion_reason: str | None
    parameters: dict[str, Any]


@dataclass(frozen=True)
class RiskDecisionView:
    """One control-by-control risk verdict, exactly as it was persisted (RM-005)."""

    outcome: RiskOutcome
    reason: str
    checks: dict[str, Any]
    volume: Decimal | None
    risk_eur: Decimal | None
    margin_eur: Decimal | None
    decided_at: datetime


@dataclass(frozen=True)
class OrderView:
    """The order the risk verdict authorized, with the broker's return code."""

    id: int
    idempotency_key: str
    symbol: str
    direction: Direction
    volume: Decimal
    requested_price: float
    stop_loss: float
    take_profit: float | None
    mode: TradingMode
    state: str
    broker_order_ticket: int | None
    retcode: int | None
    broker_comment: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PositionView:
    """The position the fill opened: entry, volume, stop, target and mode."""

    broker_position_ticket: int
    symbol: str
    direction: Direction
    volume: Decimal
    open_price: float
    stop_loss: float
    take_profit: float | None
    mode: TradingMode
    state: str
    opened_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ExecutionEventView:
    """One telemetry hop, with the latency the runtime measured for it.

    ``elapsed_ms`` is read from the stored payload: it is a measurement, not a difference
    the dashboard recomputes. A hop that carries none stays ``None`` and is shown ``n/a``.
    """

    id: int
    kind: ExecutionEventKind
    symbol: str
    detail: dict[str, Any]
    elapsed_ms: int | None
    occurred_at: datetime


@dataclass(frozen=True)
class TradeReplay:
    """One signal and everything that followed it, resolved through the real foreign keys.

    A signal that never became a position — refused by the risk engine, expired, rejected —
    is a legitimate replay: the signal, its version and its verdict are there, and
    ``order``, ``position`` and ``trade`` are ``None``. The page states that rather than
    pretending the chain is broken.
    """

    signal: SignalView
    strategy: StrategyVersionView
    risk: RiskDecisionView | None
    order: OrderView | None
    executions: tuple[ExecutionView, ...]
    position: PositionView | None
    trade: Trade | None
    close_price: float | None
    exit_reason: str | None
    events: tuple[ExecutionEventView, ...]
    analyses: tuple[AnalysisView, ...]

    @property
    def signal_id(self) -> int:
        return self.signal.id

    @property
    def r_multiple(self) -> Decimal | None:
        """Realized R, by the storage layer's own definition (``DailyPerformance``):
        net profit divided by the risk the engine authorized. A ratio of two stored
        columns, never a re-derivation of a figure the analytics package owns."""
        if self.trade is None or self.trade.risk_eur == 0:
            return None
        return self.trade.pnl_eur / self.trade.risk_eur

    @property
    def total_duration(self) -> timedelta | None:
        """Signal to close, the difference between two stored timestamps."""
        if self.trade is None:
            return None
        return self.trade.closed_at - self.signal.generated_at


def _signal_view(signal: SignalRow) -> SignalView:
    return SignalView(
        id=int(signal.id),
        idempotency_key=signal.idempotency_key,
        symbol=signal.symbol,
        timeframe=signal.timeframe,
        direction=signal.direction,
        mode=signal.mode,
        observed_price=signal.observed_price,
        entry_low=signal.entry_low,
        entry_high=signal.entry_high,
        stop_loss=signal.stop_loss,
        take_profits=tuple(float(value) for value in signal.take_profits),
        reason=signal.reason,
        # Sorted once, here: the stored JSON has no meaningful order and the page must read
        # the same way on every render.
        indicators={key: float(value) for key, value in sorted(signal.indicators.items())},
        generated_at=signal.generated_at,
        expires_at=signal.expires_at,
        state=str(signal.state),
    )


def _strategy_view(
    version: StrategyVersionRow, registry: StrategyRegistryRow | None
) -> StrategyVersionView:
    return StrategyVersionView(
        ref=version.ref,
        strategy_id=version.strategy_id,
        version=version.version,
        content_hash=version.content_hash,
        manifest=dict(version.manifest),
        first_seen_at=version.first_seen_at,
        status=None if registry is None else registry.status,
        origin=None if registry is None else registry.origin,
        promoted_at=None if registry is None else registry.promoted_at,
        promotion_reason=None if registry is None else registry.promotion_reason,
        parameters={} if registry is None else dict(registry.parameters),
    )


def _order_view(order: OrderRow) -> OrderView:
    return OrderView(
        id=int(order.id),
        idempotency_key=order.idempotency_key,
        symbol=order.symbol,
        direction=order.direction,
        volume=order.volume,
        requested_price=order.requested_price,
        stop_loss=order.stop_loss,
        take_profit=order.take_profit,
        mode=order.mode,
        state=str(order.state),
        broker_order_ticket=order.broker_order_ticket,
        retcode=order.retcode,
        broker_comment=order.broker_comment,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


def _position_detail(position: PositionRow) -> PositionView:
    return PositionView(
        broker_position_ticket=position.broker_position_ticket,
        symbol=position.symbol,
        direction=position.direction,
        volume=position.volume,
        open_price=position.open_price,
        stop_loss=position.stop_loss,
        take_profit=position.take_profit,
        mode=position.mode,
        state=str(position.state),
        opened_at=position.opened_at,
        updated_at=position.updated_at,
    )


def _elapsed_ms(detail: dict[str, Any]) -> int | None:
    """The measured hop latency, when the runtime recorded one. Booleans are not numbers."""
    value = detail.get("elapsed_ms")
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _event_view(event: ExecutionEventRow) -> ExecutionEventView:
    detail = dict(event.detail)
    return ExecutionEventView(
        id=int(event.id),
        kind=event.kind,
        symbol=event.symbol,
        detail=detail,
        elapsed_ms=_elapsed_ms(detail),
        occurred_at=event.occurred_at,
    )


def _analysis_view(row: AiAnalysisRow) -> AnalysisView:
    return AnalysisView(
        id=int(row.id),
        kind=str(row.kind),
        market=row.market,
        ref=row.ref,
        model=row.model,
        findings=dict(row.findings),
        response=row.response,
        cost_eur=row.cost_eur,
        created_at=row.created_at,
    )


def trade_replay(engine: Engine, signal_id: int) -> TradeReplay | None:
    """The full replay of one signal, or ``None`` when no such signal exists.

    The chain is walked through the stored foreign keys — signal → version, signal →
    decision, signal → order → position → trade — so a missing link yields an explicit
    empty section instead of an exception.
    """
    with Session(engine) as session:
        signal = session.get(SignalRow, signal_id)
        if signal is None:
            return None
        version = session.get(StrategyVersionRow, signal.strategy_version_id)
        if version is None:
            return None
        registry = session.scalars(
            select(StrategyRegistryRow).where(
                StrategyRegistryRow.market == signal.symbol,
                StrategyRegistryRow.ref == version.ref,
            )
        ).first()
        decision = session.scalars(
            select(RiskDecisionRow)
            .where(RiskDecisionRow.signal_id == signal.id)
            .order_by(desc(RiskDecisionRow.id))
            .limit(1)
        ).first()
        order = session.scalars(
            select(OrderRow).where(OrderRow.signal_id == signal.id).order_by(OrderRow.id)
        ).first()
        position = (
            None
            if order is None
            else session.scalars(
                select(PositionRow).where(PositionRow.order_id == order.id).order_by(PositionRow.id)
            ).first()
        )
        trade_row = (
            None
            if position is None
            else session.scalars(
                select(TradeRow).where(TradeRow.position_id == position.id).order_by(TradeRow.id)
            ).first()
        )
        executions = (
            ()
            if order is None
            else tuple(
                ExecutionView(
                    price=execution.price,
                    volume=execution.volume,
                    slippage=execution.slippage,
                    executed_at=execution.executed_at,
                    broker_deal_ticket=execution.broker_deal_ticket,
                )
                for execution in session.scalars(
                    select(ExecutionRow)
                    .where(ExecutionRow.order_id == order.id)
                    .order_by(ExecutionRow.executed_at, ExecutionRow.id)
                ).all()
            )
        )
        events = session.scalars(
            select(ExecutionEventRow)
            .where(_attached_to(int(signal.id), None if order is None else int(order.id)))
            .order_by(ExecutionEventRow.occurred_at, ExecutionEventRow.id)
        ).all()
        analyses = session.scalars(
            select(AiAnalysisRow)
            .where(AiAnalysisRow.signal_id == signal.id)
            .order_by(AiAnalysisRow.created_at, AiAnalysisRow.id)
        ).all()
        close_price = None if trade_row is None else trade_row.close_price
        exit_reason = None if trade_row is None else trade_row.exit_reason
        trade = (
            None
            if trade_row is None or position is None
            else Trade(
                symbol=position.symbol,
                strategy_ref=version.ref,
                direction=position.direction,
                timeframe=signal.timeframe,
                mode=trade_row.mode,
                opened_at=position.opened_at,
                closed_at=trade_row.closed_at,
                pnl_eur=trade_row.pnl_eur,
                risk_eur=trade_row.risk_eur,
            )
        )

    return TradeReplay(
        signal=_signal_view(signal),
        strategy=_strategy_view(version, registry),
        risk=None
        if decision is None
        else RiskDecisionView(
            outcome=decision.outcome,
            reason=decision.reason,
            checks=dict(decision.checks),
            volume=decision.volume,
            risk_eur=decision.risk_eur,
            margin_eur=decision.margin_eur,
            decided_at=decision.decided_at,
        ),
        order=None if order is None else _order_view(order),
        executions=executions,
        position=None if position is None else _position_detail(position),
        trade=trade,
        close_price=close_price,
        exit_reason=exit_reason,
        events=tuple(_event_view(event) for event in events),
        analyses=tuple(_analysis_view(row) for row in analyses),
    )


def _attached_to(signal_id: int, order_id: int | None) -> Any:
    """Telemetry is linked either to the signal or to the order it belongs to."""
    if order_id is None:
        return ExecutionEventRow.signal_id == signal_id
    return or_(
        ExecutionEventRow.signal_id == signal_id,
        ExecutionEventRow.order_id == order_id,
    )


# ---------------------------------------------------------------------------------------
# §28: the market conditions at the moment of the replayed signal.
#
# Everything here is read or already computed: the candles are the stored ones, the
# indicators are the ones the signal recorded, the volatility regime and the session come
# from the daily pass's own pure functions (`ai.daily`), and the slippage is the one the
# execution row holds. No indicator is derived from the candles — a chart is not an
# indicator — and an absence stays an absence: ``None``, read ``n/a`` (§34).
# ---------------------------------------------------------------------------------------

# The window drawn around `signals.generated_at`: 30 candles of context before the signal,
# 10 after it. Past the limit the candle is simply not part of the chart.
REPLAY_CANDLES_BEFORE = 30
REPLAY_CANDLES_AFTER = 10
# Under this, the "chart" would be two or three strokes that say nothing. The page then
# states that no candles are stored for this instant instead of drawing a shape (§51's own
# rule: a chart is earned).
REPLAY_MIN_CANDLES = 10
REPLAY_CHART_WIDTH = 960
REPLAY_CHART_HEIGHT = 240
REPLAY_CHART_PADDING_TOP = 18.0
REPLAY_CHART_PADDING_BOTTOM = 26.0
# How many recent signals of the same market feed the volatility median. Bounded by the
# signal's own moment, never by the clock: the same replay reads the same regime every time.
REPLAY_REGIME_WINDOW = 30
# The ATR spellings the daily pass reads (`ai.daily._atr`): one stored field, one meaning.
_ATR_KEYS: tuple[str, ...] = ("atr", "ATR", "atr14")


@dataclass(frozen=True)
class CandleView:
    """One stored candle, as recorded: prices go to the page as they are in the table."""

    open_time: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class CandleChart:
    """Candles already mapped into a viewBox — display geometry, never an indicator.

    ``signal_x`` is where the signal's own timestamp falls on the drawn axis, ``None`` when
    it is outside the window; it is a position, not a market level.
    """

    width: int
    height: int
    timeframe: Timeframe
    high: float
    low: float
    plot_top: float
    plot_bottom: float
    candles: tuple[CandleMark, ...]
    signal_x: float | None
    first_at: datetime
    last_at: datetime


@dataclass(frozen=True)
class MarketContext:
    """What the market looked like when the replayed signal was generated (§28).

    Every field is either read from a stored row or returned by a pure function the daily
    pass already uses. ``None`` means the database holds nothing for it, and the page writes
    ``n/a``: the dashboard fills no gap with a plausible number.
    """

    symbol: str
    candles: tuple[CandleView, ...]
    chart: CandleChart | None
    indicators: dict[str, float]
    atr: float | None
    median_atr: float | None
    regime: str | None
    session: str
    observed_price: float
    executed_price: float | None
    slippage: float | None


def _stored_atr(indicators: Mapping[str, Any] | None) -> float | None:
    """The ATR a signal recorded, under the keys the daily pass itself looks for."""
    if not indicators:
        return None
    for key in _ATR_KEYS:
        value = indicators.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        return float(value)
    return None


def _median(values: Sequence[float]) -> float | None:
    """The upper median of the sorted values — the daily pass's own definition."""
    known = sorted(values)
    return known[len(known) // 2] if known else None


def _candles_around(
    engine: Engine, symbol: str, timeframe: Timeframe, at: datetime
) -> tuple[CandleView, ...]:
    """The stored candles of one unit around ``at``, in chronological order.

    Two bounded reads rather than one: the window is asymmetric on purpose (more context
    behind a signal than ahead of it). It is bounded in *time* as well as in rows, using the
    timeframe's own duration, so a lonely candle written hours away cannot be drawn as if it
    sat next to the others — the axis places candles by index, and a stale row would lie.
    """
    step = timedelta(seconds=timeframe.seconds)
    restricted = (CandleRow.symbol == symbol, CandleRow.timeframe == timeframe)
    with Session(engine) as session:
        earlier = session.scalars(
            select(CandleRow)
            .where(
                *restricted,
                CandleRow.open_time > at - step * REPLAY_CANDLES_BEFORE,
                CandleRow.open_time <= at,
            )
            .order_by(desc(CandleRow.open_time), desc(CandleRow.id))
            .limit(REPLAY_CANDLES_BEFORE)
        ).all()
        later = session.scalars(
            select(CandleRow)
            .where(
                *restricted,
                CandleRow.open_time > at,
                CandleRow.open_time <= at + step * REPLAY_CANDLES_AFTER,
            )
            .order_by(CandleRow.open_time, CandleRow.id)
            .limit(REPLAY_CANDLES_AFTER)
        ).all()
    ordered = sorted([*earlier, *later], key=lambda row: (row.open_time, row.id))
    return tuple(
        CandleView(
            open_time=row.open_time,
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
        )
        for row in ordered
    )


def _stored_timeframes(engine: Engine, symbol: str) -> tuple[Timeframe, ...]:
    """The units this market stored candles in, most recently written first."""
    statement = (
        select(CandleRow.timeframe, func.max(CandleRow.open_time))
        .where(CandleRow.symbol == symbol)
        .group_by(CandleRow.timeframe)
    )
    with Session(engine) as session:
        rows = session.execute(statement).all()
    return tuple(timeframe for timeframe, _ in sorted(rows, key=lambda row: row[1], reverse=True))


def _replay_candles(
    engine: Engine, symbol: str, preferred: Timeframe, at: datetime
) -> tuple[tuple[CandleView, ...], Timeframe | None]:
    """The candles the chart draws, and the unit they are in.

    The signal's own timeframe comes first — it is the unit the strategy reasoned in. When it
    does not hold enough stored candles, the market's other units are tried, richest first,
    because a chart must never mix two units in one view. Which unit was traced is returned,
    so the page can say it rather than let the reader assume.
    """
    best = _candles_around(engine, symbol, preferred, at)
    traced: Timeframe | None = preferred
    if len(best) >= REPLAY_MIN_CANDLES:
        return best, traced
    for candidate in _stored_timeframes(engine, symbol):
        if candidate == preferred:
            continue
        candles = _candles_around(engine, symbol, candidate, at)
        if len(candles) > len(best):
            best, traced = candles, candidate
        if len(best) >= REPLAY_MIN_CANDLES:
            break
    return best, traced


def _chart(candles: Sequence[CandleView], at: datetime, timeframe: Timeframe) -> CandleChart | None:
    """The candles mapped into a viewBox. Display geometry only: no figure is derived here.

    Under ``REPLAY_MIN_CANDLES`` there is no chart at all — the page says the database holds
    no candles for this instant instead of drawing a line through three points.
    """
    if len(candles) < REPLAY_MIN_CANDLES:
        return None
    high = max(candle.high for candle in candles)
    low = min(candle.low for candle in candles)
    span = high - low or 1.0
    top = REPLAY_CHART_PADDING_TOP
    bottom = float(REPLAY_CHART_HEIGHT) - REPLAY_CHART_PADDING_BOTTOM
    slot = REPLAY_CHART_WIDTH / len(candles)
    width = max(1.5, slot * 0.42)

    def y(value: float) -> float:
        return bottom - (value - low) / span * (bottom - top)

    marks = tuple(
        CandleMark(
            x=index * slot + slot / 2,
            width=width,
            wick_top=y(candle.high),
            wick_bottom=y(candle.low),
            body_top=y(max(candle.open, candle.close)),
            body_bottom=y(min(candle.open, candle.close)),
            up=candle.close >= candle.open,
        )
        for index, candle in enumerate(candles)
    )
    before = sum(1 for candle in candles if candle.open_time <= at)
    return CandleChart(
        width=REPLAY_CHART_WIDTH,
        height=REPLAY_CHART_HEIGHT,
        timeframe=timeframe,
        high=high,
        low=low,
        plot_top=top,
        plot_bottom=bottom,
        candles=marks,
        signal_x=slot * before if 0 < before < len(candles) else None,
        first_at=candles[0].open_time,
        last_at=candles[-1].open_time,
    )


def _market_median_atr(engine: Engine, symbol: str, at: datetime) -> float | None:
    """The median ATR of this market's last signals, up to the replayed one.

    ``at`` bounds the window rather than the clock, so a later signal can neither change an
    old trade's regime nor make the same page read differently twice.
    """
    statement = (
        select(SignalRow.indicators)
        .where(SignalRow.symbol == symbol, SignalRow.generated_at <= at)
        .order_by(desc(SignalRow.generated_at), desc(SignalRow.id))
        .limit(REPLAY_REGIME_WINDOW)
    )
    with Session(engine) as session:
        payloads = session.scalars(statement).all()
    return _median([atr for payload in payloads if (atr := _stored_atr(payload)) is not None])


def market_context(engine: Engine, replay: TradeReplay) -> MarketContext:
    """The market conditions at the moment of the replayed signal (§28).

    The candles are the stored ones around ``signals.generated_at``; the indicators are the
    ones the signal recorded; the volatility regime compares that ATR to the median of the
    market's last signals; the session is the UTC hour of the signal; the observed and
    executed prices and the slippage come from the signal, the order and its fills. Nothing
    is recomputed and nothing is interpolated: a missing value stays ``None``.
    """
    signal = replay.signal
    candles, timeframe = _replay_candles(
        engine, signal.symbol, signal.timeframe, signal.generated_at
    )
    atr = _stored_atr(signal.indicators)
    median_atr = _market_median_atr(engine, signal.symbol, signal.generated_at)
    execution = replay.executions[0] if replay.executions else None
    return MarketContext(
        symbol=signal.symbol,
        candles=candles,
        chart=None if timeframe is None else _chart(candles, signal.generated_at, timeframe),
        indicators=dict(signal.indicators),
        atr=atr,
        median_atr=median_atr,
        # `regime_of` is the daily pass's own comparison; its "inconnu" is not a verdict this
        # page should spell differently, so an absent ATR or median stays `None` here.
        regime=None if atr is None or median_atr is None else regime_of(atr, median_atr),
        session=session_of(signal.generated_at),
        observed_price=signal.observed_price,
        executed_price=None if execution is None else execution.price,
        slippage=None if execution is None else execution.slippage,
    )


# ---------------------------------------------------------------------------------------
# Account, exposure and the overview page.
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AccountFigures:
    """Balance, equity and the equity peak, read from the recorded snapshots."""

    balance: Decimal | None
    equity: Decimal | None
    at: datetime | None
    peak_equity: Decimal | None
    drawdown: Decimal | None


def latest_snapshot(engine: Engine, at: datetime) -> Snapshot | None:
    return AccountStore(engine).latest_before(at)


def equity_series(engine: Engine) -> list[tuple[datetime, Decimal]]:
    statement = select(AccountSnapshotRow.at, AccountSnapshotRow.equity).order_by(
        AccountSnapshotRow.at
    )
    with Session(engine) as session:
        rows = session.execute(statement).all()
    return [(moment, Decimal(value)) for moment, value in rows]


def account_figures(engine: Engine, at: datetime) -> AccountFigures:
    """The account's recorded state.

    The drawdown shown here is the fall from the recorded equity peak — the difference
    between two stored snapshots, not a re-derivation of the analytics drawdown, which
    stays on the overview page as ``Performance.max_drawdown``.
    """
    snapshot = latest_snapshot(engine, at)
    peak: Decimal | None = None
    for _, value in equity_series(engine):
        peak = value if peak is None else max(peak, value)
    equity = snapshot.equity if snapshot is not None else None
    if equity is not None:
        peak = equity if peak is None else max(peak, equity)
    return AccountFigures(
        balance=snapshot.balance if snapshot is not None else None,
        equity=equity,
        at=snapshot.at if snapshot is not None else None,
        peak_equity=peak,
        drawdown=None if peak is None or equity is None else max(Decimal(0), peak - equity),
    )


@dataclass(frozen=True)
class OpenPositionView:
    """One open position, plus its age and notional at the moment the page was rendered.

    ``notional`` is ``volume * open_price``: an aggregation of two stored columns, shown as
    an order of magnitude. True exposure needs the contract size, which the database does
    not hold; the page labels the column accordingly.
    """

    symbol: str
    direction: Direction
    volume: Decimal
    open_price: float
    mode: TradingMode
    opened_at: datetime
    age: timedelta
    notional: Decimal


def open_positions(
    engine: Engine, at: datetime, market: str = paging.ALL_MARKETS
) -> tuple[OpenPositionView, ...]:
    """Every open position, or only those of one market when one is named.

    The live feed carries the same filter as the page it feeds: an unfiltered count next to a
    filtered table would contradict the table.
    """
    return tuple(
        _position_view(row, at)
        for row in PositionReader(engine).open_positions(symbol=market or None)
    )


def _position_view(position: OpenPosition, at: datetime) -> OpenPositionView:
    return OpenPositionView(
        symbol=position.symbol,
        direction=position.direction,
        volume=position.volume,
        open_price=position.open_price,
        mode=position.mode,
        opened_at=position.opened_at,
        age=at - position.opened_at,
        notional=position.volume * Decimal(str(position.open_price)),
    )


# ---------------------------------------------------------------------------------------
# The positions page: the most recent positions, one bounded page at a time.
#
# The table grows without bound over a trading year, so the ten rows the operator sees are
# chosen by SQLite, not by the browser: the search is a bound parameter and the page is a
# LIMIT/OFFSET. Sending the whole history to display ten rows is the defect this prevents.
# ---------------------------------------------------------------------------------------

POSITION_PAGE_SIZE = 10
# What a search may match, in the operator's terms. The ticket is an integer column and the
# enums are stored as their values, so every one of them is cast to text before the LIKE.
POSITION_SEARCH_FIELDS: tuple[str, ...] = (
    "symbole",
    "sens",
    "mode",
    "ticket",
    "motif de sortie",
)


@dataclass(frozen=True)
class PositionListView:
    """One position of the history, closed or still open, as the page displays it."""

    ticket: int
    symbol: str
    direction: Direction
    mode: TradingMode
    state: PositionState
    volume: Decimal
    open_price: float
    notional: Decimal
    opened_at: datetime
    age: timedelta
    exit_reason: str | None


def available_markets(engine: Engine) -> tuple[str, ...]:
    """Every market the database knows, sorted.

    Read from the positions and the signals: a market that has produced a signal but no
    position yet must appear in the selector, otherwise the operator cannot look at it. This
    is a read of stored rows, never a guess from the configuration file.
    """
    symbols = {*_distinct(engine, PositionRow.symbol), *_distinct(engine, SignalRow.symbol)}
    return tuple(sorted(symbols))


def position_page(
    engine: Engine,
    at: datetime,
    *,
    query: str = "",
    market: str = paging.ALL_MARKETS,
    page: int = 1,
    page_size: int = POSITION_PAGE_SIZE,
) -> paging.Page[PositionListView]:
    """The most recent positions, one page, filtered and bounded in the database.

    Newest first, ``opened_at`` and then ``id`` so two positions opened in the same second
    keep a stable order across pages. The count and the page are two statements on purpose:
    the count must not carry the LIMIT. ``market`` is a bound parameter: the positions page
    shows one instrument at a time, never both.
    """
    needle = query.strip()
    size = max(1, page_size)
    conditions = _position_search(needle)
    if market:
        conditions.append(PositionRow.symbol == market)
    count_statement = (
        select(func.count())
        .select_from(PositionRow)
        .outerjoin(TradeRow, TradeRow.position_id == PositionRow.id)
        .where(*conditions)
    )
    with Session(engine) as session:
        total = int(session.scalar(count_statement) or 0)
        current, pages = paging.page_bounds(total, page, size)
        statement = (
            select(PositionRow, TradeRow.exit_reason)
            .outerjoin(TradeRow, TradeRow.position_id == PositionRow.id)
            .where(*conditions)
            .order_by(desc(PositionRow.opened_at), desc(PositionRow.id))
            .limit(size)
            .offset((current - 1) * size)
        )
        found = session.execute(statement).all()
    return paging.Page(
        rows=tuple(_position_list_view(row[0], row[1], at) for row in found),
        total=total,
        page=current,
        pages=pages,
        page_size=size,
        query=needle,
        market=market,
        path="/positions",
        noun="position",
        feminine=True,
    )


def _position_list_view(
    position: PositionRow, exit_reason: str | None, at: datetime
) -> PositionListView:
    return PositionListView(
        ticket=position.broker_position_ticket,
        symbol=position.symbol,
        direction=position.direction,
        mode=position.mode,
        state=position.state,
        volume=position.volume,
        open_price=position.open_price,
        notional=position.volume * Decimal(str(position.open_price)),
        opened_at=position.opened_at,
        age=at - position.opened_at,
        exit_reason=exit_reason,
    )


def _position_search(needle: str) -> list[Any]:
    """One OR over the useful columns, every branch a bound parameter.

    The pattern is escaped: ``%`` and ``_`` typed by the operator are search text, not the
    SQL wildcards they resemble. The enums are cast to text first — a LIKE pattern cannot go
    through the column's own type, which validates its values.
    """
    if not needle:
        return []
    pattern = _like_pattern(needle)
    terms: list[Any] = [
        cast(PositionRow.symbol, String).ilike(pattern, escape="\\"),
        cast(PositionRow.broker_position_ticket, String).ilike(pattern, escape="\\"),
        cast(TradeRow.exit_reason, String).ilike(pattern, escape="\\"),
    ]
    terms.extend(
        _enum_search_terms(PositionRow.direction, list(Direction), display.direction_label, needle)
    )
    terms.extend(
        _enum_search_terms(PositionRow.mode, list(TradingMode), display.mode_label, needle)
    )
    return [or_(*terms)]


def _enum_search_terms(
    column: Any, members: Iterable[Any], label: Callable[[Any], str], needle: str
) -> list[Any]:
    """Match an enum column by its stored value or by the label the page displays."""
    terms: list[Any] = [cast(column, String).ilike(_like_pattern(needle), escape="\\")]
    folded = needle.casefold()
    terms.extend(column == member for member in members if folded in label(member).casefold())
    return terms


def _like_pattern(needle: str) -> str:
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@dataclass(frozen=True)
class MarketSummary:
    """One market's performance, computed by the shared analytics package."""

    market: str
    performance: Performance
    open_positions: int
    notional: Decimal


def market_summaries(
    trades: Sequence[Trade], positions: Sequence[OpenPositionView]
) -> tuple[MarketSummary, ...]:
    buckets = group(trades, Axis.MARKET)
    markets = sorted({*buckets, *(position.symbol for position in positions)})
    return tuple(
        MarketSummary(
            market=market,
            performance=compute_performance(buckets.get(market, [])),
            open_positions=sum(1 for position in positions if position.symbol == market),
            notional=sum(
                (position.notional for position in positions if position.symbol == market),
                Decimal(0),
            ),
        )
        for market in markets
    )


@dataclass(frozen=True)
class BacktestView:
    """A stored backtest. Its metrics are displayed as recorded, never recomputed."""

    ref: str
    market: str
    dataset_id: str
    fingerprint: str
    window_start: datetime
    window_end: datetime
    metrics: dict[str, Any]
    costs: dict[str, Any]
    report_path: str | None
    created_at: datetime


@dataclass(frozen=True)
class ValidationView:
    """One validation gate and its stored verdict."""

    ref: str
    market: str
    stage: ValidationStage
    passed: bool
    detail: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class StrategyView:
    """One line of the strategies page: registry state plus realized performance."""

    ref: str
    market: str
    strategy_id: str
    version: str
    status: StrategyStatus | None
    origin: str | None
    promoted_at: datetime | None
    updated_at: datetime | None
    promotion_reason: str | None
    performance: Performance
    last_backtest: BacktestView | None
    validations: tuple[ValidationView, ...]


def backtest_runs(engine: Engine, limit: int = 200) -> tuple[BacktestView, ...]:
    statement = select(BacktestRunRow).order_by(desc(BacktestRunRow.created_at)).limit(limit)
    with Session(engine) as session:
        rows = session.scalars(statement).all()
    return tuple(_backtest(row) for row in rows)


def _backtest(row: BacktestRunRow) -> BacktestView:
    return BacktestView(
        ref=row.ref,
        market=row.market,
        dataset_id=row.dataset_id,
        fingerprint=row.fingerprint,
        window_start=row.window_start,
        window_end=row.window_end,
        metrics=dict(row.metrics),
        costs=dict(row.costs),
        report_path=row.report_path,
        created_at=row.created_at,
    )


def validation_runs(engine: Engine, limit: int = 500) -> tuple[ValidationView, ...]:
    statement = select(ValidationRunRow).order_by(desc(ValidationRunRow.created_at)).limit(limit)
    with Session(engine) as session:
        rows = session.scalars(statement).all()
    return tuple(
        ValidationView(
            ref=row.ref,
            market=row.market,
            stage=row.stage,
            passed=row.passed,
            detail=dict(row.detail),
            created_at=row.created_at,
        )
        for row in rows
    )


def strategies(engine: Engine, trades: Sequence[Trade] | None = None) -> tuple[StrategyView, ...]:
    """The registry, plus any strategy that already produced trades without a registry row.

    A reference seen in the trades but absent from the registry is shown with an empty
    status rather than hidden: a missing deployment state is exactly what the operator
    needs to see.
    """
    history = list(all_trades(engine) if trades is None else trades)
    buckets = group(history, Axis.STRATEGY)
    with Session(engine) as session:
        registry = session.scalars(
            select(StrategyRegistryRow).order_by(
                StrategyRegistryRow.market, StrategyRegistryRow.ref
            )
        ).all()
    backtests = backtest_runs(engine)
    validations = validation_runs(engine)

    rows: dict[str, StrategyView] = {}
    for row in registry:
        rows[row.ref] = StrategyView(
            ref=row.ref,
            market=row.market,
            strategy_id=row.strategy_id,
            version=row.version,
            status=row.status,
            origin=row.origin,
            promoted_at=row.promoted_at,
            updated_at=row.updated_at,
            promotion_reason=row.promotion_reason,
            performance=compute_performance(_for_ref(history, row.ref, row.market)),
            last_backtest=_latest_backtest(backtests, row.ref, row.market),
            validations=tuple(
                item for item in validations if item.ref == row.ref and item.market == row.market
            ),
        )
    for ref, bucket in buckets.items():
        if ref in rows:
            continue
        market = bucket[0].symbol if bucket else ""
        strategy_id, _, version = ref.partition("@")
        rows[ref] = StrategyView(
            ref=ref,
            market=market,
            strategy_id=strategy_id,
            version=version,
            status=None,
            origin=None,
            promoted_at=None,
            updated_at=None,
            promotion_reason=None,
            performance=compute_performance(bucket),
            last_backtest=_latest_backtest(backtests, ref, market),
            validations=tuple(
                item for item in validations if item.ref == ref and item.market == market
            ),
        )
    return tuple(sorted(rows.values(), key=lambda view: (view.market, view.ref)))


def _for_ref(trades: Sequence[Trade], ref: str, market: str) -> list[Trade]:
    return [trade for trade in trades if trade.strategy_ref == ref and trade.symbol == market]


def _latest_backtest(
    backtests: Sequence[BacktestView], ref: str, market: str
) -> BacktestView | None:
    for candidate in backtests:  # newest first
        if candidate.ref == ref and candidate.market == market:
            return candidate
    return None


# ---------------------------------------------------------------------------------------
# The Guardian EAs, read from the bridge's JSON reports (§24, §47).
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EaView:
    """One Guardian EA as its own report describes it.

    There is nothing recomputed and nothing invented here: the heartbeat, the connection
    state, the applied revision, the kill switch and the journal are what the EA wrote. A
    report the dashboard cannot parse is reported ``OFFLINE``, because an unreadable
    Guardian is not a working Guardian (:mod:`tradingagent.ea.health`).
    """

    symbol: str
    status: EaStatus
    readable: bool
    heartbeat_at: datetime | None
    heartbeat_age: timedelta | None
    connected: bool
    trade_allowed: bool
    kill_switch: bool
    local_halt: bool
    halt_reason: str
    ea_version: str
    protocol_version: int
    applied_revision: int
    remote_positions: int
    counters: dict[str, Any]
    events: tuple[EaEvent, ...]

    @property
    def online(self) -> bool:
        return self.status is EaStatus.ONLINE

    @property
    def halted(self) -> bool:
        """Either the operator's kill switch or the EA's own watchdog stopped the hand."""
        return self.kill_switch or self.local_halt


def ea_views(reports_dir: Path | None, at: datetime) -> tuple[EaView, ...]:
    """What the Guardian EAs last wrote, one entry per symbol, oldest symbol first.

    ``reports_dir`` is ``None`` when the bridge is not installed: the answer is then an
    empty tuple, which the pages render as "no EA configured" rather than as a failure.
    """
    if reports_dir is None:
        return ()
    reports = read_reports(reports_dir, now=_frozen(at), timeout_seconds=_EA_TIMEOUT_SECONDS)
    views = [_ea_view(report, at) for report in reports]
    known = {view.symbol for view in views}
    # A corrupt report never reaches `read_reports`; `ea_health` keys on the file name
    # instead, so an installed-but-unreadable EA is never silently missing from the page.
    statuses = ea_health(reports_dir, now=_frozen(at), timeout_seconds=_EA_TIMEOUT_SECONDS)
    views.extend(
        _unreadable_ea(symbol, status) for symbol, status in statuses.items() if symbol not in known
    )
    return tuple(sorted(views, key=lambda view: view.symbol))


def _frozen(at: datetime) -> Callable[[], datetime]:
    """The bridge takes a clock; the dashboard's clock is the one injected into the app."""
    return lambda: at


def _ea_view(report: EaReport, at: datetime) -> EaView:
    return EaView(
        symbol=report.symbol,
        status=report.status,
        readable=True,
        heartbeat_at=report.heartbeat_at,
        heartbeat_age=at - report.heartbeat_at,
        connected=report.connected,
        trade_allowed=report.trade_allowed,
        kill_switch=report.kill_switch,
        local_halt=report.local_halt,
        halt_reason=report.halt_reason,
        ea_version=report.ea_version,
        protocol_version=report.protocol_version,
        applied_revision=report.applied_revision,
        remote_positions=len(report.positions),
        counters=dict(report.counters),
        events=report.events,
    )


def _unreadable_ea(symbol: str, status: EaStatus) -> EaView:
    return EaView(
        symbol=symbol,
        status=status,
        readable=False,
        heartbeat_at=None,
        heartbeat_age=None,
        connected=False,
        trade_allowed=False,
        kill_switch=False,
        local_halt=False,
        halt_reason="",
        ea_version="",
        protocol_version=0,
        applied_revision=0,
        remote_positions=0,
        counters={},
        events=(),
    )


def ea_halted(views: Sequence[EaView]) -> tuple[EaView, ...]:
    """The Guardians whose kill switch or local watchdog is active."""
    return tuple(view for view in views if view.halted)


# ---------------------------------------------------------------------------------------
# The daily aggregate table, read as stored (§31, §33).
# ---------------------------------------------------------------------------------------


def daily_performance(
    engine: Engine, start: datetime, end: datetime, limit: int = 60
) -> tuple[DailyPerformance, ...]:
    """``daily_performance`` rows as the storage layer returns them, newest day first.

    The dashboard displays the stored aggregates and the storage layer's own ratios
    (``win_rate``, ``r_multiple``); it never re-buckets them itself. The table is the
    designated fast path for the trend view and may legitimately be empty on a fresh
    install, in which case the page simply omits it.
    """
    rows = DailyPerformanceStore(engine).between(start, end)
    return tuple(reversed(rows))[:limit]


def daily_window(at: datetime, days: int = 30) -> tuple[datetime, datetime]:
    """The UTC window the daily table covers: the last ``days`` whole days, inclusive."""
    first = day_start(at) - timedelta(days=days - 1)
    return first, day_start(at) + timedelta(days=1)


@dataclass(frozen=True)
class Overview:
    """Everything the landing page states, all of it read or already computed."""

    at: datetime
    account: AccountFigures
    total: Performance
    markets: tuple[MarketSummary, ...]
    positions: tuple[OpenPositionView, ...]
    pnl_day: Decimal
    pnl_week: Decimal
    pnl_month: Decimal
    strategies: tuple[StrategyView, ...]
    halt: HaltStatus
    ea: tuple[EaView, ...]
    daily: tuple[DailyPerformance, ...]


def overview(engine: Engine, at: datetime, ea_reports_dir: Path | None = None) -> Overview:
    """Assemble the landing page from the database, the analytics package and the EAs."""
    trades = all_trades(engine)
    positions = open_positions(engine, at)
    start, end = daily_window(at)
    return Overview(
        at=at,
        account=account_figures(engine, at),
        total=compute_performance(trades),
        markets=market_summaries(trades, positions),
        positions=positions,
        pnl_day=compute_performance(trades_between(engine, day_start(at), at)).net_profit,
        pnl_week=compute_performance(trades_between(engine, week_start(at), at)).net_profit,
        pnl_month=compute_performance(trades_between(engine, month_start(at), at)).net_profit,
        strategies=strategies(engine, trades),
        halt=halt_status(engine),
        ea=ea_views(ea_reports_dir, at),
        daily=daily_performance(engine, start, end),
    )


# ---------------------------------------------------------------------------------------
# AI laboratory: analyses, hypotheses, proposals, validations.
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProposalView:
    """A hypothesis waiting for validation; it becomes production only through the gates."""

    id: int
    market: str
    ref: str | None
    analysis_id: int | None
    hypothesis: str
    proposed_change: dict[str, Any]
    status: str
    decided_by: str | None
    decision_reason: str | None
    created_at: datetime
    decided_at: datetime | None


@dataclass(frozen=True)
class AiLab:
    analyses: tuple[AnalysisView, ...]
    proposals: tuple[ProposalView, ...]
    validations: tuple[ValidationView, ...]
    backtests: tuple[BacktestView, ...]


def ai_analyses(engine: Engine, limit: int = 100) -> tuple[AnalysisView, ...]:
    statement = select(AiAnalysisRow).order_by(desc(AiAnalysisRow.created_at)).limit(limit)
    with Session(engine) as session:
        rows = session.scalars(statement).all()
    return tuple(_analysis_view(row) for row in rows)


def ai_proposals(engine: Engine, limit: int = 100) -> tuple[ProposalView, ...]:
    statement = select(AiProposalRow).order_by(desc(AiProposalRow.created_at)).limit(limit)
    with Session(engine) as session:
        rows = session.scalars(statement).all()
    return tuple(
        ProposalView(
            id=int(row.id),
            market=row.market,
            ref=row.ref,
            analysis_id=row.analysis_id,
            hypothesis=row.hypothesis,
            proposed_change=dict(row.proposed_change),
            status=str(row.status),
            decided_by=row.decided_by,
            decision_reason=row.decision_reason,
            created_at=row.created_at,
            decided_at=row.decided_at,
        )
        for row in rows
    )


def ai_lab(engine: Engine) -> AiLab:
    return AiLab(
        analyses=ai_analyses(engine),
        proposals=ai_proposals(engine),
        validations=validation_runs(engine),
        backtests=backtest_runs(engine),
    )


# ---------------------------------------------------------------------------------------
# Risk: exposures, configured limits, events and the current halt.
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ExposureView:
    market: str
    open_positions: int
    notional: Decimal


@dataclass(frozen=True)
class RiskLimits:
    """One configured risk profile, read from ``config/agent.yaml`` and validated by the
    very schema the agent uses. Displaying a limit is not recomputing it (RM-005, RM-006)."""

    profile: str
    risk_per_trade_pct: Decimal
    daily_loss_pct: Decimal
    weekly_loss_pct: Decimal
    max_drawdown_pct: Decimal
    max_open_positions: int
    max_positions_per_market: int
    max_trades_per_day: int
    cooldown_after_losses: int
    cooldown_hours: Decimal
    max_spread_stop_pct: Decimal
    margin_usage_pct: Decimal
    max_volume: Decimal | None
    reference_capital: Decimal | None
    currency: str | None


def risk_limits(path: Path) -> tuple[RiskLimits, ...]:
    """The ``simulated`` and ``live`` profiles of the agent's configuration file.

    Returns an empty tuple when the file is missing or invalid: a dashboard must still
    render while the agent's configuration is being edited, and it must not invent limits.
    """
    try:
        document = read_yaml(path)
        config = RiskConfig.model_validate(document.data["risk"])
    except (ConfigError, ValidationError, KeyError, TypeError):
        return ()
    return (
        _limits("simulé", config.simulated, None, None),
        _limits("réel", config.live, config.live.reference_capital, config.live.currency),
    )


def _limits(
    profile: str,
    source: RiskProfile,
    reference_capital: Decimal | None,
    currency: str | None,
) -> RiskLimits:
    return RiskLimits(
        profile=profile,
        risk_per_trade_pct=source.risk_per_trade_pct,
        daily_loss_pct=source.daily_loss_pct,
        weekly_loss_pct=source.weekly_loss_pct,
        max_drawdown_pct=source.max_drawdown_pct,
        max_open_positions=source.max_open_positions,
        max_positions_per_market=source.max_positions_per_market,
        max_trades_per_day=source.max_trades_per_day,
        cooldown_after_losses=source.cooldown_after_losses,
        cooldown_hours=source.cooldown_hours,
        max_spread_stop_pct=source.max_spread_stop_pct,
        margin_usage_pct=source.margin_usage_pct,
        max_volume=source.max_volume,
        reference_capital=reference_capital,
        currency=currency,
    )


@dataclass(frozen=True)
class SystemEventView:
    kind: str
    severity: Severity
    detail: dict[str, Any]
    occurred_at: datetime
    # The instrument the event belongs to, when the caller knew one. Account-wide events
    # (clock mismatch, kill switch, mode command) carry none, and none is ever guessed.
    symbol: str | None = None


@dataclass(frozen=True)
class HaltView:
    scope: str
    action: HaltAction
    source: HaltSource
    actor: str
    reason: str
    close_positions: bool
    occurred_at: datetime


@dataclass(frozen=True)
class RiskView:
    at: datetime
    halt: HaltStatus
    history: Page[HaltView]
    halted_pairs: tuple[tuple[str, str], ...]
    halted_markets: tuple[str, ...]
    exposures: tuple[ExposureView, ...]
    limits: tuple[RiskLimits, ...]
    refusals: RefusedRisk
    events: Page[SystemEventView]
    ea: tuple[EaView, ...]


def halt_status(engine: Engine, scopes: Iterable[str] = TRADING_SCOPES) -> HaltStatus:
    """The current trading state, fail-closed: an unreadable state counts as halted."""
    return HaltStore(engine).status(scopes)


def halt_history(engine: Engine, limit: int = 30) -> tuple[HaltView, ...]:
    """The most recent halt and resume commands across every scope."""
    statement = select(HaltCommandRow).order_by(desc(HaltCommandRow.id)).limit(limit)
    with Session(engine) as session:
        rows = session.scalars(statement).all()
    return tuple(
        HaltView(
            scope=row.scope,
            action=row.action,
            source=row.source,
            actor=row.actor,
            reason=row.reason,
            close_positions=row.close_positions,
            occurred_at=row.occurred_at,
        )
        for row in rows
    )


def halted_pairs(engine: Engine) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(HaltStore(engine).halted_pairs()))


def halted_markets(engine: Engine) -> tuple[str, ...]:
    return tuple(sorted(HaltStore(engine).halted_markets()))


def exposures(positions: Sequence[OpenPositionView]) -> tuple[ExposureView, ...]:
    markets = sorted({position.symbol for position in positions})
    return tuple(
        ExposureView(
            market=market,
            open_positions=sum(1 for position in positions if position.symbol == market),
            notional=sum(
                (position.notional for position in positions if position.symbol == market),
                Decimal(0),
            ),
        )
        for market in markets
    )


def recent_events(
    engine: Engine,
    *,
    since: datetime | None = None,
    minimum: Severity | None = None,
    limit: int = 50,
) -> tuple[SystemEventView, ...]:
    """System events, newest first: the risk page, the system page and the SSE alert feed."""
    statement = select(SystemEventRow).order_by(desc(SystemEventRow.occurred_at))
    if since is not None:
        statement = statement.where(SystemEventRow.occurred_at > since)
    if minimum is not None:
        statement = statement.where(SystemEventRow.severity != Severity.INFO)
    with Session(engine) as session:
        rows = session.scalars(statement.limit(limit)).all()
    return tuple(
        SystemEventView(
            kind=row.kind,
            severity=row.severity,
            detail=dict(row.detail),
            occurred_at=row.occurred_at,
            symbol=row.symbol,
        )
        for row in rows
    )


def risk_view(
    engine: Engine,
    at: datetime,
    limits_path: Path,
    ea_reports_dir: Path | None = None,
    *,
    market: str = paging.ALL_MARKETS,
    query: str = "",
    events_page: int = 1,
    history_page: int = 1,
) -> RiskView:
    """The risk page: the state, and the two logs that grow without bound.

    ``system_events`` carries the instrument of an event (``symbol``, null when the event
    concerns the account as a whole), so the events are separated by market — a market's own
    events plus the account-wide ones, never another instrument's. ``halt_commands`` scopes
    its commands with a free-form ``scope`` rather than a market column: that list stays
    paged and searched, with no market filter it could honestly apply.
    """
    positions = open_positions(engine, at)
    events = event_page(
        engine,
        market=market,
        query=query,
        page=events_page,
        path="/risk",
        noun="événement",
        filters=(("hpage", str(history_page)),),
    )
    history = halt_page(
        engine,
        query=query,
        page=history_page,
        filters=((events.page_param, str(events_page)),),
    )
    return RiskView(
        at=at,
        halt=halt_status(engine),
        history=history,
        halted_pairs=halted_pairs(engine),
        halted_markets=halted_markets(engine),
        exposures=exposures(positions),
        limits=risk_limits(limits_path),
        refusals=ReportData(engine).refused_risk_between(EPOCH, FOREVER),
        events=events,
        ea=ea_views(ea_reports_dir, at),
    )


# ---------------------------------------------------------------------------------------
# The two logs of the risk page, and the two of the system page: one shape, four uses.
#
# Both live in append-only tables that grow all year, so both are counted and bounded in
# SQL, and both answer a search that is a bound parameter. `system_events` now carries the
# instrument of the event (`symbol`, nullable) and `execution_events` always has: the market
# is therefore a query parameter here too — with one rule of its own, below.
# ---------------------------------------------------------------------------------------

RISK_PAGE_SIZE = paging.PAGE_SIZE
EVENT_SEARCH_FIELDS: tuple[str, ...] = ("type", "marché", "gravité", "détail")
HALT_SEARCH_FIELDS: tuple[str, ...] = ("portée", "action", "source", "acteur", "motif")
TELEMETRY_SEARCH_FIELDS: tuple[str, ...] = ("type", "marché", "détail")
# What the events table writes when an event belongs to no market: the account as a whole.
ACCOUNT_WIDE = "tous marchés"


def event_markets(engine: Engine) -> tuple[str, ...]:
    """The markets the events name, plus the ones the database trades.

    A market that has only written events — never a position or a signal — must still be
    offered, otherwise its events would be unreachable behind the default.
    """
    named = [
        str(value)
        for value in _distinct(engine, SystemEventRow.symbol)
        if value is not None and str(value)
    ]
    return tuple(sorted({*named, *available_markets(engine)}))


def _event_market(column: Any, market: str) -> Any | None:
    """One market's events, **plus the account-wide ones**, which belong to every market.

    ``system_events.symbol`` is null for what concerns the account as a whole — a clock
    mismatch, a kill switch, a mode command. Filtering on equality alone would hide them
    behind every market selector, which on a risk page is the one alert the operator must
    not lose. Two instruments still never share a table: BTCUSD events stay out of the
    XAUUSD page. An empty market is not a market: it means "nothing to separate" and adds
    no condition at all.
    """
    if not market:
        return None
    return or_(column == market, column.is_(None))


def event_page(
    engine: Engine,
    *,
    market: str = paging.ALL_MARKETS,
    query: str = "",
    minimum: Severity | None = None,
    page: int = 1,
    page_size: int = RISK_PAGE_SIZE,
    path: str = "/risk",
    noun: str = "événement",
    feminine: bool = False,
    page_param: str = "page",
    filters: tuple[tuple[str, str], ...] = (),
) -> Page[SystemEventView]:
    """One page of system events, newest first, filtered, searched and bounded in SQL."""
    needle = query.strip()
    scoped = _event_market(SystemEventRow.symbol, market)
    conditions: list[Any] = [] if scoped is None else [scoped]
    if minimum is not None:
        conditions.append(SystemEventRow.severity != Severity.INFO)
    conditions.extend(
        paging.text_search(
            needle,
            (
                SystemEventRow.kind,
                SystemEventRow.symbol,
                SystemEventRow.severity,
                SystemEventRow.detail,
            ),
        )
    )
    with Session(engine) as session:
        found = _slice(
            session,
            SystemEventRow,
            conditions,
            (desc(SystemEventRow.occurred_at), desc(SystemEventRow.id)),
            page,
            max(1, page_size),
        )
    return Page(
        rows=tuple(
            SystemEventView(
                kind=row.kind,
                severity=row.severity,
                detail=dict(row.detail),
                occurred_at=row.occurred_at,
                symbol=row.symbol,
            )
            for row in found.rows
        ),
        total=found.total,
        page=found.page,
        pages=found.pages,
        page_size=max(1, page_size),
        query=needle,
        market=market,
        path=path,
        noun=noun,
        feminine=feminine,
        page_param=page_param,
        filters=filters,
    )


def halt_page(
    engine: Engine,
    *,
    query: str = "",
    page: int = 1,
    page_size: int = RISK_PAGE_SIZE,
    filters: tuple[tuple[str, str], ...] = (),
) -> Page[HaltView]:
    """One page of halt and resume commands, newest first, searched and bounded in SQL."""
    needle = query.strip()
    conditions = paging.text_search(
        needle,
        (
            HaltCommandRow.scope,
            HaltCommandRow.action,
            HaltCommandRow.source,
            HaltCommandRow.actor,
            HaltCommandRow.reason,
        ),
    )
    with Session(engine) as session:
        found = _slice(
            session,
            HaltCommandRow,
            conditions,
            (desc(HaltCommandRow.id),),
            page,
            max(1, page_size),
        )
    return Page(
        rows=tuple(
            HaltView(
                scope=row.scope,
                action=row.action,
                source=row.source,
                actor=row.actor,
                reason=row.reason,
                close_positions=row.close_positions,
                occurred_at=row.occurred_at,
            )
            for row in found.rows
        ),
        total=found.total,
        page=found.page,
        pages=found.pages,
        page_size=max(1, page_size),
        query=needle,
        path="/risk",
        noun="commande d'arrêt",
        feminine=True,
        page_param="hpage",
        filters=filters,
    )


def telemetry_page(
    engine: Engine,
    *,
    market: str = paging.ALL_MARKETS,
    query: str = "",
    page: int = 1,
    page_size: int = paging.PAGE_SIZE,
    filters: tuple[tuple[str, str], ...] = (),
) -> Page[TelemetryView]:
    """One page of execution telemetry, newest first, filtered, searched and bounded in SQL.

    ``execution_events.symbol`` is never null — a hop belongs to an order, and an order to an
    instrument — so here the market is a plain equality, with no account-wide case.
    """
    needle = query.strip()
    conditions: list[Any] = []
    if market:
        conditions.append(ExecutionEventRow.symbol == market)
    conditions.extend(
        paging.text_search(
            needle, (ExecutionEventRow.kind, ExecutionEventRow.symbol, ExecutionEventRow.detail)
        )
    )
    with Session(engine) as session:
        found = _slice(
            session,
            ExecutionEventRow,
            conditions,
            (desc(ExecutionEventRow.occurred_at), desc(ExecutionEventRow.id)),
            page,
            max(1, page_size),
        )
    return Page(
        rows=tuple(
            TelemetryView(
                id=int(row.id),
                kind=row.kind,
                symbol=row.symbol,
                detail=dict(row.detail),
                occurred_at=row.occurred_at,
            )
            for row in found.rows
        ),
        total=found.total,
        page=found.page,
        pages=found.pages,
        page_size=max(1, page_size),
        query=needle,
        market=market,
        path="/system",
        noun="événement d'exécution",
        filters=filters,
    )


# ---------------------------------------------------------------------------------------
# System health: database, market freshness, execution latency, last errors.
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DatabaseHealth:
    ok: bool
    dialect: str
    error: str | None


@dataclass(frozen=True)
class MarketHealth:
    """One market's observable freshness.

    There is no EA heartbeat table yet (cahier v3 §47): rather than invent a status, the
    dashboard shows the two things the database does record — the newest stored candle and
    the newest execution event — and the page says so.
    """

    symbol: str
    last_candle_at: datetime | None
    candle_age: timedelta | None
    last_event_at: datetime | None
    last_event_kind: ExecutionEventKind | None


@dataclass(frozen=True)
class TelemetryView:
    id: int
    kind: ExecutionEventKind
    symbol: str
    detail: dict[str, object]
    occurred_at: datetime


@dataclass(frozen=True)
class LatencyView:
    label: str
    stats: LatencyStats


@dataclass(frozen=True)
class JournalEntry:
    """One line of a Guardian's journal, with the market it came from.

    The journal is the only list of this page that does not live in the database: it is read
    from the bridge's JSON reports, so it cannot be counted and paged in SQL. It is
    therefore *bounded* — the newest lines first, and the total is stated on the page — and
    never rendered whole.
    """

    symbol: str
    at: datetime
    seq: int
    severity: str
    kind: str
    message: str
    ticket: int | None


@dataclass(frozen=True)
class SystemView:
    at: datetime
    database: DatabaseHealth
    markets: tuple[MarketHealth, ...]
    latencies: tuple[LatencyView, ...]
    telemetry: Page[TelemetryView]
    errors: Page[SystemEventView]
    journal: tuple[JournalEntry, ...]
    journal_total: int
    halt: HaltStatus
    ea: tuple[EaView, ...]


# How many journal lines the system page draws, and the ceiling the table itself never
# exceeds: one display, like every other list of the dashboard.
EA_JOURNAL_LIMIT = paging.PAGE_SIZE


def database_health(engine: Engine) -> DatabaseHealth:
    """A single ``SELECT 1``: the dashboard reports the database, it does not repair it."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as error:  # a health page must render the failure, not raise it
        return DatabaseHealth(ok=False, dialect=engine.dialect.name, error=repr(error))
    return DatabaseHealth(ok=True, dialect=engine.dialect.name, error=None)


def market_health(
    engine: Engine, at: datetime, symbols: Sequence[str] = ()
) -> tuple[MarketHealth, ...]:
    """Freshness per market, from the newest stored candle and the newest execution event.

    The traded symbols are taken from the positions, the candles and the telemetry already
    in the database, so the page works without reading the agent's configuration file.
    """
    known = sorted(
        {
            *symbols,
            *_distinct(engine, PositionRow.symbol),
            *_distinct(engine, CandleRow.symbol),
            *_distinct(engine, ExecutionEventRow.symbol),
        }
    )
    health: list[MarketHealth] = []
    with Session(engine) as session:
        for symbol in known:
            candle_at = session.scalar(
                select(CandleRow.open_time)
                .where(CandleRow.symbol == symbol)
                .order_by(desc(CandleRow.open_time))
                .limit(1)
            )
            latest = session.execute(
                select(ExecutionEventRow.occurred_at, ExecutionEventRow.kind)
                .where(ExecutionEventRow.symbol == symbol)
                .order_by(desc(ExecutionEventRow.occurred_at))
                .limit(1)
            ).first()
            health.append(
                MarketHealth(
                    symbol=symbol,
                    last_candle_at=candle_at,
                    candle_age=None if candle_at is None else at - candle_at,
                    last_event_at=latest[0] if latest is not None else None,
                    last_event_kind=latest[1] if latest is not None else None,
                )
            )
    return tuple(health)


def _distinct(engine: Engine, column: Any) -> list[str]:
    with Session(engine) as session:
        return [str(value) for value in session.scalars(select(column).distinct()).all()]


LATENCY_PAIRS: tuple[tuple[str, ExecutionEventKind, ExecutionEventKind], ...] = (
    ("Signal → ordre envoyé", ExecutionEventKind.SIGNAL_GENERATED, ExecutionEventKind.ORDER_SENT),
    ("Ordre envoyé → accepté", ExecutionEventKind.ORDER_SENT, ExecutionEventKind.ORDER_ACCEPTED),
    ("Ordre accepté → exécuté", ExecutionEventKind.ORDER_ACCEPTED, ExecutionEventKind.FILLED),
    ("Signal → exécuté", ExecutionEventKind.SIGNAL_GENERATED, ExecutionEventKind.FILLED),
)


def latency_views(engine: Engine) -> tuple[LatencyView, ...]:
    """Measured hop latencies, computed by :class:`ExecutionEventStore` from stored events."""
    store = ExecutionEventStore(engine)
    return tuple(
        LatencyView(label=label, stats=store.latency(start, end))
        for label, start, end in LATENCY_PAIRS
    )


def telemetry(engine: Engine, limit: int = 25) -> tuple[TelemetryView, ...]:
    return tuple(
        TelemetryView(
            id=event.id,
            kind=event.kind,
            symbol=event.symbol,
            detail=event.detail,
            occurred_at=event.occurred_at,
        )
        for event in ExecutionEventStore(engine).recent(limit=limit)
    )


def ea_journal(views: Sequence[EaView], limit: int = EA_JOURNAL_LIMIT) -> tuple[JournalEntry, ...]:
    """The newest journal lines of every Guardian, merged, bounded to one display.

    A report carries every event the EA chose to write; a Guardian that has been running for
    a month can carry hundreds. The page draws the newest ``limit`` of them across all the
    EAs and says how many were read: a truncated list that hides its own truncation is the
    defect this replaces.
    """
    entries = [
        JournalEntry(
            symbol=view.symbol,
            at=event.at,
            seq=event.seq,
            severity=event.severity,
            kind=event.kind,
            message=event.message,
            ticket=event.ticket,
        )
        for view in views
        for event in view.events
    ]
    return tuple(sorted(entries, key=lambda entry: (entry.at, entry.seq), reverse=True)[:limit])


def system_view(
    engine: Engine,
    at: datetime,
    symbols: Sequence[str] = (),
    ea_reports_dir: Path | None = None,
    *,
    market: str = paging.ALL_MARKETS,
    query: str = "",
    page: int = 1,
    errors_page: int = 1,
) -> SystemView:
    """The health page: the probes, and the three lists that can grow.

    The four market probes (freshness, Guardians, data, latencies) describe every market, as
    a health page must; the two logs answer the market selector, and the errors keep the
    account-wide events visible whatever the selected market is.
    """
    views = ea_views(ea_reports_dir, at)
    telemetry = telemetry_page(
        engine,
        market=market,
        query=query,
        page=page,
        filters=(("epage", str(errors_page)),),
    )
    errors = event_page(
        engine,
        market=market,
        query=query,
        minimum=Severity.WARNING,
        page=errors_page,
        path="/system",
        noun="erreur",
        feminine=True,
        page_param="epage",
        filters=((telemetry.page_param, str(page)),),
    )
    return SystemView(
        at=at,
        database=database_health(engine),
        markets=market_health(engine, at, symbols),
        latencies=latency_views(engine),
        telemetry=telemetry,
        errors=errors,
        journal=ea_journal(views),
        journal_total=sum(len(view.events) for view in views),
        halt=halt_status(engine),
        ea=views,
    )


# ---------------------------------------------------------------------------------------
# Reports.
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportView:
    id: int
    period: str
    window_start: datetime
    window_end: datetime
    content: str
    sent_at: datetime | None
    generated_at: datetime


def reports(engine: Engine, limit: int = 50) -> tuple[ReportView, ...]:
    statement = select(ReportRow).order_by(desc(ReportRow.window_start)).limit(limit)
    with Session(engine) as session:
        rows = session.scalars(statement).all()
    return tuple(_report(row) for row in rows)


REPORT_PAGE_SIZE = paging.PAGE_SIZE
REPORT_SEARCH_FIELDS: tuple[str, ...] = ("période", "contenu")


def report_page(
    engine: Engine,
    *,
    query: str = "",
    page: int = 1,
    page_size: int = REPORT_PAGE_SIZE,
) -> Page[ReportView]:
    """One page of stored reports, newest window first, searched and bounded in SQL.

    A stored report carries no market column (``reports`` holds a period, a window and the
    text that was generated): there is nothing to separate by instrument here, and the page
    says so rather than inventing a market selector that would filter nothing.
    """
    needle = query.strip()
    size = max(1, page_size)
    conditions = paging.text_search(needle, (ReportRow.period, ReportRow.content))
    with Session(engine) as session:
        total = int(
            session.scalar(select(func.count()).select_from(ReportRow).where(*conditions)) or 0
        )
        current, pages = paging.page_bounds(total, page, size)
        rows = session.scalars(
            select(ReportRow)
            .where(*conditions)
            .order_by(desc(ReportRow.window_start), desc(ReportRow.id))
            .limit(size)
            .offset((current - 1) * size)
        ).all()
    return Page(
        rows=tuple(_report(row) for row in rows),
        total=total,
        page=current,
        pages=pages,
        page_size=size,
        query=needle,
        path="/reports",
        noun="rapport",
    )


def report_by_id(engine: Engine, report_id: int) -> ReportView | None:
    with Session(engine) as session:
        row = session.get(ReportRow, report_id)
    return None if row is None else _report(row)


def _report(row: ReportRow) -> ReportView:
    return ReportView(
        id=int(row.id),
        period=row.period,
        window_start=row.window_start,
        window_end=row.window_end,
        content=row.content,
        sent_at=row.sent_at,
        generated_at=row.generated_at,
    )


# ---------------------------------------------------------------------------------------
# One page of any table, counted and bounded in SQL.
#
# Every list page below asks for its ten rows through this one function, so "the count and
# the LIMIT are in the database" is stated once instead of once per page. Nothing is ever
# read whole and sliced afterwards: the defect that made a page carry four hundred rows to
# display ten cannot come back through a copy of the paging code.
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Slice:
    """What the database returned for one page: the rows, and the count behind them."""

    rows: list[Any]
    total: int
    page: int
    pages: int


def _slice(
    session: Session,
    table: Any,
    conditions: Sequence[Any],
    order_by: Sequence[Any],
    page: int,
    size: int,
    *,
    entities: bool = True,
) -> _Slice:
    """Count then limit. ``entities`` is ``False`` for a subquery, whose rows are tuples."""
    total = int(session.scalar(select(func.count()).select_from(table).where(*conditions)) or 0)
    current, pages = paging.page_bounds(total, page, size)
    statement = (
        select(table)
        .where(*conditions)
        .order_by(*order_by)
        .limit(size)
        .offset((current - 1) * size)
    )
    rows = list(session.scalars(statement)) if entities else list(session.execute(statement).all())
    return _Slice(rows=rows, total=total, page=current, pages=pages)


def _filtered(market: str, column: Any, needle: str, columns: Sequence[Any]) -> list[Any]:
    """The market, then the search — every branch a bound parameter."""
    conditions: list[Any] = []
    if market:
        conditions.append(column == market)
    conditions.extend(paging.text_search(needle, columns))
    return conditions


def _siblings(**pages: int) -> tuple[tuple[str, str], ...]:
    """The other lists' page numbers, carried by a page link so they do not move."""
    return tuple((name, str(value)) for name, value in pages.items())


# ---------------------------------------------------------------------------------------
# The strategies page: one market at a time, ten rows, counted and searched in SQL.
#
# A strategy the dashboard can show comes from one of two places: the registry, which is the
# deployment state, and the trades themselves, because a reference that produced trades
# without a registry row is exactly what the operator needs to see. Both are filtered in
# SQL; their union is what gets counted and paged, so a registry of four hundred rows is
# never read to display ten.
# ---------------------------------------------------------------------------------------

STRATEGY_PAGE_SIZE = paging.PAGE_SIZE
STRATEGY_SEARCH_FIELDS: tuple[str, ...] = ("référence", "stratégie", "version", "marché")


@dataclass(frozen=True)
class StrategyListing:
    """One page of strategies, and the markets the selector may offer."""

    page: Page[StrategyView]
    markets: tuple[str, ...]


def _strategy_identities() -> Any:
    """(marché, référence, stratégie, version) of every strategy the database knows.

    ``UNION`` rather than ``UNION ALL``: a reference that is both registered and traded is
    one line, not two.
    """
    registry = select(
        StrategyRegistryRow.market.label("market"),
        StrategyRegistryRow.ref.label("ref"),
        StrategyRegistryRow.strategy_id.label("strategy_id"),
        StrategyRegistryRow.version.label("version"),
    )
    traded = (
        select(
            PositionRow.symbol.label("market"),
            StrategyVersionRow.ref.label("ref"),
            StrategyVersionRow.strategy_id.label("strategy_id"),
            StrategyVersionRow.version.label("version"),
        )
        .select_from(TradeRow)
        .join(PositionRow, TradeRow.position_id == PositionRow.id)
        .join(OrderRow, PositionRow.order_id == OrderRow.id)
        .join(SignalRow, OrderRow.signal_id == SignalRow.id)
        .join(StrategyVersionRow, SignalRow.strategy_version_id == StrategyVersionRow.id)
    )
    return registry.union(traded).subquery()


def strategy_markets(engine: Engine) -> tuple[str, ...]:
    """Every market a strategy is registered for or has traded on, sorted."""
    return tuple(
        sorted({*_distinct(engine, StrategyRegistryRow.market), *available_markets(engine)})
    )


def strategy_page(
    engine: Engine,
    *,
    market: str = paging.ALL_MARKETS,
    query: str = "",
    page: int = 1,
    page_size: int = STRATEGY_PAGE_SIZE,
) -> StrategyListing:
    """The registry and its evidence, one page, filtered and bounded in the database."""
    needle = query.strip()
    size = max(1, page_size)
    identities = _strategy_identities()
    conditions = _filtered(
        market,
        identities.c.market,
        needle,
        (identities.c.ref, identities.c.strategy_id, identities.c.version, identities.c.market),
    )
    with Session(engine) as session:
        found = _slice(
            session,
            identities,
            conditions,
            (identities.c.market, identities.c.ref),
            page,
            size,
            entities=False,
        )
    return StrategyListing(
        page=Page(
            rows=_strategy_views(engine, found.rows),
            total=found.total,
            page=found.page,
            pages=found.pages,
            page_size=size,
            query=needle,
            market=market,
            path="/strategies",
            noun="stratégie",
            feminine=True,
        ),
        markets=strategy_markets(engine),
    )


def _strategy_views(engine: Engine, identities: Sequence[Any]) -> tuple[StrategyView, ...]:
    """The full view of the strategies on the page — and only of those.

    The figures and the evidence are read for the ten visible references, never for the
    whole registry: a page that computes four hundred performances to draw ten would undo
    the paging the SQL just did.
    """
    pairs = [(str(row.market), str(row.ref)) for row in identities]
    if not pairs:
        return ()
    with Session(engine) as session:
        registry = {
            (str(row.market), str(row.ref)): row
            for row in session.scalars(
                select(StrategyRegistryRow).where(
                    tuple_(StrategyRegistryRow.market, StrategyRegistryRow.ref).in_(pairs)
                )
            ).all()
        }
        trades = _trades_by_strategy(session, pairs)
    backtests = backtest_runs(engine)
    validations = validation_runs(engine)
    views: list[StrategyView] = []
    for row in identities:
        market, ref = str(row.market), str(row.ref)
        known = registry.get((market, ref))
        views.append(
            StrategyView(
                ref=ref,
                market=market,
                # A reference only the trades know keeps the identity `strategy_versions`
                # recorded for it: the dashboard invents no version.
                strategy_id=str(known.strategy_id if known is not None else row.strategy_id),
                version=str(known.version if known is not None else row.version),
                status=None if known is None else known.status,
                origin=None if known is None else known.origin,
                promoted_at=None if known is None else known.promoted_at,
                updated_at=None if known is None else known.updated_at,
                promotion_reason=None if known is None else known.promotion_reason,
                performance=compute_performance(trades.get((market, ref), [])),
                last_backtest=_latest_backtest(backtests, ref, market),
                validations=tuple(
                    item for item in validations if item.ref == ref and item.market == market
                ),
            )
        )
    return tuple(views)


def _trades_by_strategy(
    session: Session, pairs: Sequence[tuple[str, str]]
) -> dict[tuple[str, str], list[Trade]]:
    """The closed trades of the named (market, reference) pairs, grouped and read once."""
    conditions = or_(
        *[
            and_(PositionRow.symbol == market, StrategyVersionRow.ref == ref)
            for market, ref in pairs
        ]
    )
    grouped: dict[tuple[str, str], list[Trade]] = defaultdict(list)
    for row in session.execute(_trade_statement([conditions])).all():
        entry = _trade_entry(*row)
        grouped[(entry.trade.symbol, entry.trade.strategy_ref)].append(entry.trade)
    return dict(grouped)


# ---------------------------------------------------------------------------------------
# The AI laboratory: four lists, four page parameters, one market.
#
# Analyses, proposals, validations and backtests all carry a market column, so the filter is
# mechanical there. Each list is counted and bounded on its own: reading the proposals must
# not move the analyses, which is why each `Page` answers to its own query parameter.
# ---------------------------------------------------------------------------------------

AI_PAGE_SIZE = paging.PAGE_SIZE
ANALYSIS_SEARCH_FIELDS: tuple[str, ...] = ("type", "marché", "référence", "modèle", "réponse")
PROPOSAL_SEARCH_FIELDS: tuple[str, ...] = ("marché", "référence", "hypothèse", "statut", "motif")
VALIDATION_SEARCH_FIELDS: tuple[str, ...] = ("référence", "marché", "portail", "détail")
BACKTEST_SEARCH_FIELDS: tuple[str, ...] = ("référence", "marché", "jeu de données", "empreinte")


@dataclass(frozen=True)
class AiLabListing:
    analyses: Page[AnalysisView]
    proposals: Page[ProposalView]
    validations: Page[ValidationView]
    backtests: Page[BacktestView]
    markets: tuple[str, ...]


def ai_markets(engine: Engine) -> tuple[str, ...]:
    """Every market the laboratory holds a row for, plus the ones the database trades."""
    return tuple(
        sorted(
            {
                *_distinct(engine, AiAnalysisRow.market),
                *_distinct(engine, AiProposalRow.market),
                *_distinct(engine, ValidationRunRow.market),
                *_distinct(engine, BacktestRunRow.market),
                *available_markets(engine),
            }
        )
    )


def ai_lab_pages(
    engine: Engine,
    *,
    market: str = paging.ALL_MARKETS,
    query: str = "",
    analyses_page: int = 1,
    proposals_page: int = 1,
    validations_page: int = 1,
    backtests_page: int = 1,
    page_size: int = AI_PAGE_SIZE,
) -> AiLabListing:
    """The four lists of the laboratory, each one page, each bounded in the database."""
    needle = query.strip()
    size = max(1, page_size)
    with Session(engine) as session:
        analyses = _slice(
            session,
            AiAnalysisRow,
            _filtered(
                market,
                AiAnalysisRow.market,
                needle,
                (
                    AiAnalysisRow.kind,
                    AiAnalysisRow.market,
                    AiAnalysisRow.ref,
                    AiAnalysisRow.model,
                    AiAnalysisRow.response,
                    AiAnalysisRow.findings,
                ),
            ),
            (desc(AiAnalysisRow.created_at), desc(AiAnalysisRow.id)),
            analyses_page,
            size,
        )
        proposals = _slice(
            session,
            AiProposalRow,
            _filtered(
                market,
                AiProposalRow.market,
                needle,
                (
                    AiProposalRow.market,
                    AiProposalRow.ref,
                    AiProposalRow.hypothesis,
                    AiProposalRow.status,
                    AiProposalRow.decision_reason,
                    AiProposalRow.proposed_change,
                ),
            ),
            (desc(AiProposalRow.created_at), desc(AiProposalRow.id)),
            proposals_page,
            size,
        )
        validations = _slice(
            session,
            ValidationRunRow,
            _filtered(
                market,
                ValidationRunRow.market,
                needle,
                (
                    ValidationRunRow.ref,
                    ValidationRunRow.market,
                    ValidationRunRow.stage,
                    ValidationRunRow.detail,
                ),
            ),
            (desc(ValidationRunRow.created_at), desc(ValidationRunRow.id)),
            validations_page,
            size,
        )
        backtests = _slice(
            session,
            BacktestRunRow,
            _filtered(
                market,
                BacktestRunRow.market,
                needle,
                (
                    BacktestRunRow.ref,
                    BacktestRunRow.market,
                    BacktestRunRow.dataset_id,
                    BacktestRunRow.fingerprint,
                ),
            ),
            (desc(BacktestRunRow.created_at), desc(BacktestRunRow.id)),
            backtests_page,
            size,
        )
    common: dict[str, Any] = {
        "page_size": size,
        "query": needle,
        "market": market,
        "path": "/ai-lab",
    }
    return AiLabListing(
        analyses=Page(
            rows=tuple(_analysis_view(row) for row in analyses.rows),
            total=analyses.total,
            page=analyses.page,
            pages=analyses.pages,
            page_param="page",
            filters=_siblings(
                proposals=proposals.page, validations=validations.page, backtests=backtests.page
            ),
            noun="analyse",
            feminine=True,
            **common,
        ),
        proposals=Page(
            rows=tuple(_proposal(row) for row in proposals.rows),
            total=proposals.total,
            page=proposals.page,
            pages=proposals.pages,
            page_param="proposals",
            filters=_siblings(
                page=analyses.page, validations=validations.page, backtests=backtests.page
            ),
            noun="proposition",
            feminine=True,
            **common,
        ),
        validations=Page(
            rows=tuple(_validation(row) for row in validations.rows),
            total=validations.total,
            page=validations.page,
            pages=validations.pages,
            page_param="validations",
            filters=_siblings(
                page=analyses.page, proposals=proposals.page, backtests=backtests.page
            ),
            noun="validation",
            feminine=True,
            **common,
        ),
        backtests=Page(
            rows=tuple(_backtest(row) for row in backtests.rows),
            total=backtests.total,
            page=backtests.page,
            pages=backtests.pages,
            page_param="backtests",
            filters=_siblings(
                page=analyses.page, proposals=proposals.page, validations=validations.page
            ),
            noun="backtest",
            **common,
        ),
        markets=ai_markets(engine),
    )


def _proposal(row: AiProposalRow) -> ProposalView:
    return ProposalView(
        id=int(row.id),
        market=row.market,
        ref=row.ref,
        analysis_id=row.analysis_id,
        hypothesis=row.hypothesis,
        proposed_change=dict(row.proposed_change),
        status=str(row.status),
        decided_by=row.decided_by,
        decision_reason=row.decision_reason,
        created_at=row.created_at,
        decided_at=row.decided_at,
    )


def _validation(row: ValidationRunRow) -> ValidationView:
    return ValidationView(
        ref=row.ref,
        market=row.market,
        stage=row.stage,
        passed=row.passed,
        detail=dict(row.detail),
        created_at=row.created_at,
    )


# ---------------------------------------------------------------------------------------
# §32: the scalping cuts. Every figure comes from `analytics.scalping` (pure) and
# `storage.scalping` (the only layer that touches the database); the page recomputes
# nothing. The bucket edges are declared here, as configuration, never inside the maths.
# ---------------------------------------------------------------------------------------

SPREAD_EDGES: tuple[float, ...] = (0.0, 0.2, 0.5, 1.0, 2.0)
# `by_duration` cuts on seconds, the unit the module documents: 0, 15 min, 1 h, 4 h.
DURATION_EDGES: tuple[float, ...] = (0.0, 900.0, 3600.0, 14400.0)
SIZE_EDGES: tuple[Decimal, ...] = (Decimal(0), Decimal("0.01"), Decimal("0.05"), Decimal("0.10"))


@dataclass(frozen=True)
class ScalpingView:
    """The §32 cuts of one market, plus the display bound of the hourly cut.

    The market is read from the data: every trade carries its symbol, so the selection is a
    bound ``WHERE positions.symbol = :market`` and never a client-side hide. ``hours`` is a
    `Page` because that cut is the only one that can outgrow a display (24 UTC hours at
    most); the others are bounded by construction (4 sessions, 7 weekdays, 5 size bands, 5
    duration bands, 6 spread bands) and are rendered whole.
    """

    market: str
    trades: int
    hours: Page[Bucket]
    sessions: tuple[Bucket, ...]
    weekdays: tuple[Bucket, ...]
    spreads: tuple[Bucket, ...]
    durations: tuple[Bucket, ...]
    sizes: tuple[Bucket, ...]
    costs: CostSummary
    execution: ExecutionCosts


def market_trades(engine: Engine, market: str = paging.ALL_MARKETS) -> tuple[Trade, ...]:
    """Every closed trade of one market, filtered in SQL — the input of the cuts.

    A distribution needs all of its sample: unlike a log, this read is not a display of ten
    rows, and the filter that scopes it is a bound parameter like every other. An empty
    ``market`` is the only case that reads more than one instrument, and it is only ever set
    when the database holds none.
    """
    conditions: list[Any] = []
    if market:
        conditions.append(PositionRow.symbol == market)
    with Session(engine) as session:
        rows = session.execute(_trade_statement(conditions)).all()
    return tuple(_trade_entry(*row).trade for row in rows)


def scalping_view(
    engine: Engine,
    *,
    market: str = paging.ALL_MARKETS,
    page: int = 1,
    page_size: int = paging.PAGE_SIZE,
) -> ScalpingView:
    """The cuts of one market. Every figure still comes from ``analytics.scalping``."""
    trades = market_trades(engine, market)
    hours = tuple(by_hour(trades))
    size = max(1, page_size)
    current, pages = paging.page_bounds(len(hours), page, size)
    return ScalpingView(
        market=market,
        trades=len(trades),
        # The buckets are produced by the pure analytics module, so there is no table for
        # SQL to LIMIT here: the bound is applied to what `by_hour` returned, and the page
        # still carries its state in the URL like every other display. Re-deriving the
        # bucketing in SQL to page it there would be a second implementation of a figure
        # the analytics package owns (§34).
        hours=Page(
            rows=hours[(current - 1) * size : current * size],
            total=len(hours),
            page=current,
            pages=pages,
            page_size=size,
            market=market,
            path="/scalping",
            noun="découpe horaire",
            feminine=True,
        ),
        sessions=by_session(trades),
        weekdays=by_weekday(trades),
        spreads=by_spread(trades, SPREAD_EDGES),
        durations=by_duration(trades, DURATION_EDGES),
        sizes=by_size(trades, size_of(engine), SIZE_EDGES),
        costs=cost_summary(trades),
        # The execution costs are read from the telemetry of the same market: a latency
        # measured on BTCUSD under an XAUUSD distribution would describe neither.
        execution=execution_costs(engine, symbol=market or None),
    )


# ---------------------------------------------------------------------------------------
# Background watermarks: real charts, not decoration.
#
# The geometry below is display maths, not analytics. It maps numbers the dashboard already
# shows onto a viewBox — no indicator is computed, nothing is derived from them, and every
# page renders identically without the watermark. §34 holds: the dashboard re-derives no
# figure, it draws the ones it was given.
# ---------------------------------------------------------------------------------------

WATERMARK_WIDTH = 1440
WATERMARK_HEIGHT = 420
WATERMARK_CANDLES = 96
# Below these, the "chart" would be a straight line between two or three points: a streak
# across the cards, not a chart. A watermark drawn from real data needs enough real data
# behind it, so under the threshold we draw nothing at all.
WATERMARK_MIN_EQUITY_POINTS = 12
WATERMARK_MIN_CANDLES = 24


@dataclass(frozen=True)
class CandleMark:
    """One candle, already mapped into the watermark's viewBox."""

    x: float
    width: float
    wick_top: float
    wick_bottom: float
    body_top: float
    body_bottom: float
    up: bool


@dataclass(frozen=True)
class ChartWatermark:
    width: int
    height: int
    equity_line: str
    equity_area: str
    candles: tuple[CandleMark, ...]
    symbol: str | None


def _equity_geometry(series: Sequence[tuple[datetime, Decimal]]) -> tuple[str, str]:
    if len(series) < WATERMARK_MIN_EQUITY_POINTS:
        return "", ""
    values = [float(value) for _, value in series]
    low, high = min(values), max(values)
    span = high - low or 1.0
    step = WATERMARK_WIDTH / (len(values) - 1)
    # Vertical padding keeps the curve off the edges; the chart is a watermark, not a table.
    top, bottom = 40.0, float(WATERMARK_HEIGHT) - 30.0
    points = [
        (index * step, bottom - (value - low) / span * (bottom - top))
        for index, value in enumerate(values)
    ]
    line = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area = (
        f"M{points[0][0]:.1f},{points[0][1]:.1f} "
        + " ".join(f"L{x:.1f},{y:.1f}" for x, y in points[1:])
        + f" L{points[-1][0]:.1f},{WATERMARK_HEIGHT} L{points[0][0]:.1f},{WATERMARK_HEIGHT} Z"
    )
    return line, area


def _candles(engine: Engine) -> tuple[str | None, tuple[CandleMark, ...]]:
    with Session(engine) as session:
        symbol = session.scalar(
            select(CandleRow.symbol).order_by(desc(CandleRow.open_time)).limit(1)
        )
        if symbol is None:
            return None, ()
        rows = session.execute(
            select(
                CandleRow.open,
                CandleRow.high,
                CandleRow.low,
                CandleRow.close,
            )
            .where(CandleRow.symbol == symbol)
            .order_by(desc(CandleRow.open_time))
            .limit(WATERMARK_CANDLES)
        ).all()
    if not rows or len(rows) < WATERMARK_MIN_CANDLES:
        return symbol, ()
    ordered = list(reversed(rows))
    highs = [float(row.high) for row in ordered]
    lows = [float(row.low) for row in ordered]
    high, low = max(highs), min(lows)
    span = high - low or 1.0
    top, bottom = 40.0, float(WATERMARK_HEIGHT) - 30.0
    slot = WATERMARK_WIDTH / len(ordered)
    width = max(1.5, slot * 0.42)

    def y(value: float) -> float:
        return bottom - (float(value) - low) / span * (bottom - top)

    marks = tuple(
        CandleMark(
            x=index * slot + slot / 2,
            width=width,
            wick_top=y(row.high),
            wick_bottom=y(row.low),
            body_top=y(max(row.open, row.close)),
            body_bottom=y(min(row.open, row.close)),
            up=float(row.close) >= float(row.open),
        )
        for index, row in enumerate(ordered)
    )
    return str(symbol), marks


def watermark(engine: Engine) -> ChartWatermark:
    """The two background charts: the real equity curve and the real candles.

    Decoration must never cost a page. An unmigrated or unreachable database, an empty
    table, a column that is not there yet: all of it yields an empty watermark, so the
    page still renders — and the handler's own read stays the one that decides between a
    503, a 404 and a real answer.
    """
    try:
        line, area = _equity_geometry(equity_series(engine))
        symbol, candles = _candles(engine)
    except SQLAlchemyError as error:
        log.warning("watermark unavailable: %s", error)
        line, area, symbol, candles = "", "", None, ()
    return ChartWatermark(
        width=WATERMARK_WIDTH,
        height=WATERMARK_HEIGHT,
        equity_line=line,
        equity_area=area,
        candles=candles,
        symbol=symbol,
    )


# ``HaltView.scope`` is rendered as stored; ``halted_pairs`` already returns the parsed
# (strategy, market) pairs the risk page displays.
__all__ = [
    "AI_PAGE_SIZE",
    "ANALYSIS_SEARCH_FIELDS",
    "BACKTEST_SEARCH_FIELDS",
    "EA_JOURNAL_LIMIT",
    "EPOCH",
    "EVENT_SEARCH_FIELDS",
    "FOREVER",
    "HALT_SEARCH_FIELDS",
    "POSITION_PAGE_SIZE",
    "POSITION_SEARCH_FIELDS",
    "PROPOSAL_SEARCH_FIELDS",
    "RISK_PAGE_SIZE",
    "STRATEGY_PAGE_SIZE",
    "STRATEGY_SEARCH_FIELDS",
    "TELEMETRY_SEARCH_FIELDS",
    "TOTAL",
    "VALIDATION_SEARCH_FIELDS",
    "AccountFigures",
    "AiLab",
    "AiLabListing",
    "AnalysisView",
    "BacktestView",
    "CandleChart",
    "CandleMark",
    "CandleView",
    "ChartWatermark",
    "DatabaseHealth",
    "EaView",
    "ExecutionEventView",
    "ExecutionView",
    "ExposureView",
    "HaltView",
    "JournalEntry",
    "LatencyView",
    "MarketContext",
    "MarketHealth",
    "MarketSummary",
    "OpenPositionView",
    "OrderView",
    "Overview",
    "PositionListView",
    "PositionView",
    "ProposalView",
    "ReportView",
    "RiskDecisionView",
    "RiskLimits",
    "RiskView",
    "ScalpingView",
    "SignalView",
    "StrategyListing",
    "StrategyVersionView",
    "StrategyView",
    "SystemEventView",
    "SystemView",
    "TelemetryView",
    "TradeDetail",
    "TradeEntry",
    "TradeFilters",
    "TradeListing",
    "TradeReplay",
    "ValidationView",
    "account_figures",
    "ai_analyses",
    "ai_lab",
    "ai_lab_pages",
    "ai_markets",
    "ai_proposals",
    "all_trade_entries",
    "all_trades",
    "available_markets",
    "backtest_runs",
    "daily_performance",
    "daily_window",
    "database_health",
    "day_start",
    "ea_halted",
    "ea_journal",
    "ea_views",
    "equity_series",
    "event_page",
    "exposures",
    "halt_history",
    "halt_page",
    "halt_status",
    "halted_markets",
    "halted_pairs",
    "latency_views",
    "latest_snapshot",
    "market_context",
    "market_health",
    "market_summaries",
    "market_trades",
    "month_start",
    "open_positions",
    "overview",
    "position_page",
    "recent_events",
    "report_by_id",
    "report_page",
    "reports",
    "risk_limits",
    "risk_view",
    "scalping_view",
    "selectable_markets",
    "selectable_strategies",
    "strategies",
    "strategy_markets",
    "strategy_page",
    "system_view",
    "telemetry",
    "telemetry_page",
    "trade_detail",
    "trade_entries",
    "trade_page",
    "trade_replay",
    "traded_strategies",
    "trades_between",
    "validation_runs",
    "watermark",
    "week_start",
]
