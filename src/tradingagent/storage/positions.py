"""Read-only view of positions for the operator's queries (TASK-022).

Position lifecycle tracking belongs to the executors (TASK-070, TASK-082); this module
only answers "what is open right now", straight from the database of record.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import PositionState
from tradingagent.storage.models import PositionRow


@dataclass(frozen=True)
class OpenPosition:
    symbol: str
    direction: Direction
    volume: Decimal
    open_price: float
    mode: TradingMode
    opened_at: datetime


class PositionReader:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def open_positions(self) -> list[OpenPosition]:
        statement = (
            select(PositionRow)
            .where(PositionRow.state == PositionState.OPEN)
            .order_by(PositionRow.opened_at)
        )
        with Session(self._engine) as session:
            rows = session.scalars(statement).all()
        return [
            OpenPosition(
                symbol=row.symbol,
                direction=row.direction,
                volume=row.volume,
                open_price=row.open_price,
                mode=row.mode,
                opened_at=row.opened_at,
            )
            for row in rows
        ]
