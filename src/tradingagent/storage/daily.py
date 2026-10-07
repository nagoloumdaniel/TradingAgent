"""Daily aggregates per mode, market and strategy (cahier v3 §31, §33).

The dashboard's fast path: one row per day, per mode, per market and per strategy. Amounts
are Decimal and every total is computed in Python, because SQLite stores them as text
(TASK-005) and no arithmetic may run on those columns in SQL.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tradingagent.core.mode import TradingMode
from tradingagent.storage.models import DailyPerformanceRow


@dataclass(frozen=True)
class DailyPerformance:
    day: datetime
    mode: TradingMode
    market: str
    ref: str
    trades: int
    wins: int
    pnl: Decimal
    risk_eur: Decimal

    @property
    def win_rate(self) -> float | None:
        return None if self.trades == 0 else self.wins / self.trades

    @property
    def r_multiple(self) -> Decimal | None:
        """Realized R: net profit divided by the risk the engine authorized."""
        if self.risk_eur == 0:
            return None
        return self.pnl / self.risk_eur


def day_floor(at: datetime) -> datetime:
    return at.replace(hour=0, minute=0, second=0, microsecond=0)


class DailyPerformanceStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def apply_trade(
        self,
        *,
        day: datetime,
        mode: TradingMode,
        market: str,
        ref: str,
        pnl: Decimal,
        risk_eur: Decimal,
        won: bool,
        at: datetime,
    ) -> None:
        """Add one closed trade to its daily bucket, creating the bucket on first use."""
        key = day_floor(day)
        with Session(self._engine) as session:
            row = session.scalars(
                select(DailyPerformanceRow).where(
                    DailyPerformanceRow.day == key,
                    DailyPerformanceRow.mode == mode,
                    DailyPerformanceRow.market == market,
                    DailyPerformanceRow.ref == ref,
                )
            ).first()
            if row is None:
                session.add(
                    DailyPerformanceRow(
                        day=key,
                        mode=mode,
                        market=market,
                        ref=ref,
                        trades=1,
                        wins=1 if won else 0,
                        pnl=pnl,
                        risk_eur=risk_eur,
                        created_at=at,
                    )
                )
            else:
                row.trades = int(row.trades) + 1
                row.wins = int(row.wins) + (1 if won else 0)
                row.pnl = Decimal(row.pnl) + pnl
                row.risk_eur = Decimal(row.risk_eur) + risk_eur
            session.commit()

    def for_day(self, day: datetime) -> list[DailyPerformance]:
        statement = select(DailyPerformanceRow).where(DailyPerformanceRow.day == day_floor(day))
        with Session(self._engine) as session:
            rows = session.scalars(statement).all()
        return [self._row(row) for row in rows]

    def between(self, start: datetime, end: datetime) -> list[DailyPerformance]:
        statement = (
            select(DailyPerformanceRow)
            .where(DailyPerformanceRow.day >= day_floor(start), DailyPerformanceRow.day < end)
            .order_by(DailyPerformanceRow.day)
        )
        with Session(self._engine) as session:
            rows = session.scalars(statement).all()
        return [self._row(row) for row in rows]

    def totals(
        self,
        *,
        at: datetime,
        days: int,
        mode: TradingMode | None = None,
        market: str | None = None,
    ) -> DailyPerformance:
        """One aggregated bucket over the last `days` days, empty when nothing traded."""
        start = day_floor(at) - timedelta(days=days - 1)
        rows = [
            entry
            for entry in self.between(start, day_floor(at) + timedelta(days=1))
            if (mode is None or entry.mode is mode) and (market is None or entry.market == market)
        ]
        ref = rows[0].ref if len(rows) == 1 else "all"
        return DailyPerformance(
            day=start,
            mode=mode or TradingMode.SIGNAL,
            market=market or "all",
            ref=ref,
            trades=sum(entry.trades for entry in rows),
            wins=sum(entry.wins for entry in rows),
            pnl=sum((entry.pnl for entry in rows), Decimal(0)),
            risk_eur=sum((entry.risk_eur for entry in rows), Decimal(0)),
        )

    @staticmethod
    def _row(row: DailyPerformanceRow) -> DailyPerformance:
        return DailyPerformance(
            day=row.day,
            mode=row.mode,
            market=row.market,
            ref=row.ref,
            trades=int(row.trades),
            wins=int(row.wins),
            pnl=Decimal(row.pnl),
            risk_eur=Decimal(row.risk_eur),
        )
