"""Account snapshots and the reporting data they anchor (section 14.3, TASK-042).

The snapshots are written by the runtime loop when it is wired; the reports read them to
state opening and closing balances instead of guessing. Every figure a report shows is
assembled here, from the database only — the model never produces numbers (TASK-042).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Engine, func, insert, select
from sqlalchemy.orm import Session

from tradingagent.analytics import Trade
from tradingagent.core.states import RiskOutcome, Severity, SignalState
from tradingagent.storage.models import (
    AccountSnapshotRow,
    OrderRow,
    PositionRow,
    RiskDecisionRow,
    SignalRow,
    StrategyVersionRow,
    SystemEventRow,
    TradeRow,
)


@dataclass(frozen=True)
class Snapshot:
    equity: Decimal
    balance: Decimal
    at: datetime


@dataclass(frozen=True)
class RefusedRisk:
    """One risk refusal: the risk it carried is the loss it avoided (F-011)."""

    count: int
    avoided_risk_eur: Decimal


@dataclass(frozen=True)
class SignalCounts:
    total: int
    validated: int
    refused: int


class AccountStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(self, equity: Decimal, balance: Decimal, at: datetime) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(AccountSnapshotRow).values(equity=equity, balance=balance, at=at)
            )

    def latest_before(self, at: datetime) -> Snapshot | None:
        statement = (
            select(AccountSnapshotRow)
            .where(AccountSnapshotRow.at <= at)
            .order_by(AccountSnapshotRow.at.desc())
            .limit(1)
        )
        with Session(self._engine) as session:
            row = session.scalars(statement).first()
        return Snapshot(row.equity, row.balance, row.at) if row else None


class ReportData:
    """Reads everything one report window needs, straight from the database."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @property
    def engine(self) -> Engine:
        """The engine this reader was built on; the monthly comparison reuses it (TASK-093)."""
        return self._engine

    def trades_between(self, start: datetime, end: datetime) -> list[tuple[int, Trade]]:
        """Closed trades in [start, end), each keeping its producing signal id."""
        statement = (
            select(TradeRow, PositionRow, SignalRow, StrategyVersionRow)
            .join(PositionRow, TradeRow.position_id == PositionRow.id)
            .join(OrderRow, PositionRow.order_id == OrderRow.id)
            .join(SignalRow, OrderRow.signal_id == SignalRow.id)
            .join(StrategyVersionRow, SignalRow.strategy_version_id == StrategyVersionRow.id)
            .where(TradeRow.closed_at >= start, TradeRow.closed_at < end)
            .order_by(TradeRow.closed_at)
        )
        with Session(self._engine) as session:
            rows = session.execute(statement).all()
        trades: list[tuple[int, Trade]] = []
        for trade, position, signal, version in rows:
            trades.append(
                (
                    signal.id,
                    Trade(
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
            )
        return trades

    def refused_risk_between(self, start: datetime, end: datetime) -> RefusedRisk:
        statement = select(RiskDecisionRow).where(
            RiskDecisionRow.outcome == RiskOutcome.REFUSED,
            RiskDecisionRow.decided_at >= start,
            RiskDecisionRow.decided_at < end,
        )
        with Session(self._engine) as session:
            rows = session.scalars(statement).all()
        avoided = sum((row.risk_eur for row in rows if row.risk_eur is not None), Decimal(0))
        return RefusedRisk(count=len(rows), avoided_risk_eur=avoided)

    def signal_counts_between(self, start: datetime, end: datetime) -> SignalCounts:
        statement = select(SignalRow).where(
            SignalRow.generated_at >= start, SignalRow.generated_at < end
        )
        with Session(self._engine) as session:
            rows = session.scalars(statement).all()
        validated = sum(1 for row in rows if row.state is not SignalState.RISK_REJECTED)
        refused = sum(1 for row in rows if row.state is SignalState.RISK_REJECTED)
        return SignalCounts(total=len(rows), validated=validated, refused=refused)

    def anomalies_between(self, start: datetime, end: datetime) -> int:
        statement = (
            select(func.count())
            .select_from(SystemEventRow)
            .where(
                SystemEventRow.occurred_at >= start,
                SystemEventRow.occurred_at < end,
                SystemEventRow.severity != Severity.INFO,
            )
        )
        with Session(self._engine) as session:
            return int(session.scalar(statement) or 0)

    def active_markets_between(self, start: datetime, end: datetime) -> list[str]:
        statement = (
            select(SignalRow.symbol)
            .where(SignalRow.generated_at >= start, SignalRow.generated_at < end)
            .distinct()
        )
        with Session(self._engine) as session:
            return sorted(session.scalars(statement).all())
