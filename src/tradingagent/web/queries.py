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

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Engine, desc, select, text
from sqlalchemy.orm import Session

from tradingagent.analytics import Axis, Performance, Trade, compute_performance, group
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
    RiskOutcome,
    Severity,
    StrategyStatus,
    ValidationStage,
)
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
from tradingagent.storage.telemetry import ExecutionEventStore, LatencyStats

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


def open_positions(engine: Engine, at: datetime) -> tuple[OpenPositionView, ...]:
    return tuple(_position_view(row, at) for row in PositionReader(engine).open_positions())


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
class AnalysisView:
    """What the AI observed. It never decided anything (§5, §15, §16, §39)."""

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
    return tuple(
        AnalysisView(
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
        for row in rows
    )


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
    history: tuple[HaltView, ...]
    halted_pairs: tuple[tuple[str, str], ...]
    halted_markets: tuple[str, ...]
    exposures: tuple[ExposureView, ...]
    limits: tuple[RiskLimits, ...]
    refusals: RefusedRisk
    events: tuple[SystemEventView, ...]
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
        )
        for row in rows
    )


def risk_view(
    engine: Engine, at: datetime, limits_path: Path, ea_reports_dir: Path | None = None
) -> RiskView:
    positions = open_positions(engine, at)
    return RiskView(
        at=at,
        halt=halt_status(engine),
        history=halt_history(engine),
        halted_pairs=halted_pairs(engine),
        halted_markets=halted_markets(engine),
        exposures=exposures(positions),
        limits=risk_limits(limits_path),
        refusals=ReportData(engine).refused_risk_between(EPOCH, FOREVER),
        events=recent_events(engine, limit=50),
        ea=ea_views(ea_reports_dir, at),
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
class SystemView:
    at: datetime
    database: DatabaseHealth
    markets: tuple[MarketHealth, ...]
    latencies: tuple[LatencyView, ...]
    telemetry: tuple[TelemetryView, ...]
    errors: tuple[SystemEventView, ...]
    halt: HaltStatus
    ea: tuple[EaView, ...]


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


def system_view(
    engine: Engine,
    at: datetime,
    symbols: Sequence[str] = (),
    ea_reports_dir: Path | None = None,
) -> SystemView:
    return SystemView(
        at=at,
        database=database_health(engine),
        markets=market_health(engine, at, symbols),
        latencies=latency_views(engine),
        telemetry=telemetry(engine),
        errors=recent_events(engine, minimum=Severity.WARNING, limit=25),
        halt=halt_status(engine),
        ea=ea_views(ea_reports_dir, at),
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


# ``HaltView.scope`` is rendered as stored; ``halted_pairs`` already returns the parsed
# (strategy, market) pairs the risk page displays.
__all__ = [
    "EPOCH",
    "FOREVER",
    "TOTAL",
    "AccountFigures",
    "AiLab",
    "AnalysisView",
    "BacktestView",
    "DatabaseHealth",
    "EaView",
    "ExecutionView",
    "ExposureView",
    "HaltView",
    "LatencyView",
    "MarketHealth",
    "MarketSummary",
    "OpenPositionView",
    "Overview",
    "ProposalView",
    "ReportView",
    "RiskLimits",
    "RiskView",
    "StrategyView",
    "SystemEventView",
    "SystemView",
    "TelemetryView",
    "TradeDetail",
    "TradeEntry",
    "TradeFilters",
    "ValidationView",
    "account_figures",
    "ai_analyses",
    "ai_lab",
    "ai_proposals",
    "all_trade_entries",
    "all_trades",
    "backtest_runs",
    "daily_performance",
    "daily_window",
    "database_health",
    "day_start",
    "ea_halted",
    "ea_views",
    "equity_series",
    "exposures",
    "halt_history",
    "halt_status",
    "halted_markets",
    "halted_pairs",
    "latency_views",
    "latest_snapshot",
    "market_health",
    "market_summaries",
    "month_start",
    "open_positions",
    "overview",
    "recent_events",
    "report_by_id",
    "reports",
    "risk_limits",
    "risk_view",
    "selectable_markets",
    "selectable_strategies",
    "strategies",
    "system_view",
    "telemetry",
    "trade_detail",
    "trade_entries",
    "trades_between",
    "validation_runs",
    "week_start",
]
