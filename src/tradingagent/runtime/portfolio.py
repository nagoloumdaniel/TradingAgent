"""Portfolio snapshot assembled from the database (TASK-035, action 1).

The risk engine reads one immutable picture: open positions, trades of the day, daily and
weekly profit and loss, the equity peak and the losing streak. Amounts are aggregated in
Python on purpose: under SQLite they are stored as text, so no SQL sum may touch them
(TASK-005). The broker is the source of truth for open positions; this module only adds
what the database knows about the past.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from tradingagent.risk.model import AccountState, OpenPosition, PortfolioState
from tradingagent.risk.streak import longest_loss_run_today, losing_streak
from tradingagent.storage.models import AccountSnapshotRow, PositionRow, TradeRow

STREAK_LOOKBACK = 200


def day_start(at: datetime) -> datetime:
    return at.replace(hour=0, minute=0, second=0, microsecond=0)


def week_start(at: datetime) -> datetime:
    return day_start(at) - timedelta(days=at.weekday())


class PortfolioBuilder:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def build(
        self,
        *,
        account: AccountState,
        open_positions: tuple[OpenPosition, ...],
        trades_today: int,
        at: datetime,
        open_exposure_eur: Decimal | None = None,
    ) -> PortfolioState:
        snapshots = self._snapshots()
        start_day = day_start(at)
        start_week = week_start(at)
        day_equity = _equity_at_or_before(snapshots, start_day, account.equity)
        week_equity = _equity_at_or_before(snapshots, start_week, account.equity)
        peak = max([account.equity, *_equities(snapshots)])
        recent = self._recent_closes()
        streak, last_loss = losing_streak(recent)
        return PortfolioState(
            open_positions=open_positions,
            trades_today=trades_today,
            day_start_equity=day_equity,
            day_pnl=account.equity - day_equity,
            week_start_equity=week_equity,
            week_pnl=account.equity - week_equity,
            equity_peak=peak,
            consecutive_losses=streak,
            last_loss_at=last_loss,
            # The daily stop is a lock over every trade of the UTC day, not over the
            # streak lookback: a run reached this morning must survive a busy afternoon.
            losses_today=longest_loss_run_today(self._closes_since(start_day), at),
            # §21: the notional already committed. None means "not measured", which the
            # risk engine treats as a refusal rather than as zero.
            open_exposure_eur=open_exposure_eur,
        )

    def trades_opened_since(self, start: datetime, at: datetime) -> int:
        statement = (
            select(func.count())
            .select_from(PositionRow)
            .where(PositionRow.opened_at >= start, PositionRow.opened_at <= at)
        )
        with Session(self._engine) as session:
            return int(session.scalar(statement) or 0)

    def _snapshots(self) -> list[tuple[datetime, Decimal]]:
        statement = select(AccountSnapshotRow.at, AccountSnapshotRow.equity).order_by(
            AccountSnapshotRow.at
        )
        with Session(self._engine) as session:
            rows = session.execute(statement).all()
        return [(row[0], Decimal(row[1])) for row in rows]

    def _closes_since(self, start: datetime) -> list[tuple[Decimal, datetime]]:
        """Every trade closed from `start` on, in any order (the daily lock reads them all)."""
        statement = select(TradeRow.pnl_eur, TradeRow.closed_at).where(TradeRow.closed_at >= start)
        with Session(self._engine) as session:
            rows = session.execute(statement).all()
        return [(Decimal(pnl), closed_at) for pnl, closed_at in rows]

    def _recent_closes(self) -> list[tuple[Decimal, datetime]]:
        """Closed trades, newest first: what the streak functions read."""
        statement = (
            select(TradeRow.pnl_eur, TradeRow.closed_at)
            .order_by(TradeRow.closed_at.desc())
            .limit(STREAK_LOOKBACK)
        )
        with Session(self._engine) as session:
            rows = session.execute(statement).all()
        return [(Decimal(pnl), closed_at) for pnl, closed_at in rows]


def _equities(snapshots: list[tuple[datetime, Decimal]]) -> list[Decimal]:
    return [equity for _, equity in snapshots]


def _equity_at_or_before(
    snapshots: list[tuple[datetime, Decimal]], moment: datetime, fallback: Decimal
) -> Decimal:
    """The last recorded equity at or before `moment`; the current one when history is
    too short, so a fresh install does not read a fictitious loss."""
    chosen = fallback
    for at, equity in snapshots:
        if at <= moment:
            chosen = equity
        else:
            break
    return chosen
