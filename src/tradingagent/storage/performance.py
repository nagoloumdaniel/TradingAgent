"""Realized-performance figures for the operator's /performance command (TASK-022).

Everything is computed from the append-only trades table, the base of every performance
figure (models.py). The deeper analytics package (TASK-041) will share this source.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tradingagent.core.mode import TradingMode
from tradingagent.storage.models import TradeRow


@dataclass(frozen=True)
class ModePerformance:
    mode: TradingMode
    trades: int
    wins: int
    total_pnl_eur: Decimal

    @property
    def win_rate(self) -> Decimal | None:
        return Decimal(self.wins) / Decimal(self.trades) if self.trades else None


@dataclass(frozen=True)
class PerformanceSummary:
    trades: int
    wins: int
    total_pnl_eur: Decimal
    last_closed_at: datetime | None
    by_mode: tuple[ModePerformance, ...]

    @property
    def win_rate(self) -> Decimal | None:
        return Decimal(self.wins) / Decimal(self.trades) if self.trades else None


class PerformanceReader:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def summary(self) -> PerformanceSummary:
        with Session(self._engine) as session:
            rows = session.scalars(select(TradeRow).order_by(TradeRow.closed_at)).all()
        by_mode: dict[TradingMode, ModePerformance] = {}
        for row in rows:
            current = by_mode.get(
                row.mode,
                ModePerformance(mode=row.mode, trades=0, wins=0, total_pnl_eur=Decimal(0)),
            )
            won = row.pnl_eur > 0
            by_mode[row.mode] = ModePerformance(
                mode=row.mode,
                trades=current.trades + 1,
                wins=current.wins + (1 if won else 0),
                total_pnl_eur=current.total_pnl_eur + row.pnl_eur,
            )
        return PerformanceSummary(
            trades=len(rows),
            wins=sum(1 for row in rows if row.pnl_eur > 0),
            total_pnl_eur=sum((row.pnl_eur for row in rows), Decimal(0)),
            last_closed_at=rows[-1].closed_at if rows else None,
            by_mode=tuple(by_mode.values()),
        )
