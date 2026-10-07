"""The executor's ledger: orders, fills, positions and closed trades (TASK-070/081/082).

The executor is the single writer of `orders`, `positions`, `executions` and `trades`, and
`OrderJournal` satisfies `ports.TradeLog` on its own, so the composition root can hand it
straight to a broker. PAPER and DEMO write the very same rows and are told apart by `mode`
alone, so the analytics code reads one stream and cannot tell the difference (F-015).

The *intent* is stored before anything leaves the process: a crash between the two leaves a
trace to reconcile, never a silent order. The order's unique idempotency key makes the retry
a lookup, not a second order. RM-018 transitions are applied here; a transition the state
machine refuses is logged, never forced.
"""

import logging
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Engine, Select, select, update
from sqlalchemy.orm import Session

from tradingagent.core.mode import TradingMode
from tradingagent.core.states import OrderState, PositionState, SignalState
from tradingagent.execution.ports import LocalPosition, OrderSnapshot
from tradingagent.risk.model import BrokerPosition, ClosedPosition, OrderRequest, OrderResult
from tradingagent.signals.lifecycle import IllegalTransitionError
from tradingagent.storage._conflicts import insert_ignoring_duplicates
from tradingagent.storage.models import (
    ExecutionRow,
    OrderRow,
    PositionRow,
    RiskDecisionRow,
    TradeRow,
)
from tradingagent.storage.signals import transition

log = logging.getLogger(__name__)
ZERO = Decimal(0)


def order_state_of(result: OrderResult) -> OrderState:
    """How one broker answer lands in `orders.state`. A lost answer is an error to reconcile,
    never a refusal a retry could reinterpret as "not sent"."""
    if result.accepted:
        return OrderState.FILLED if result.stop_present else OrderState.ERROR
    if result.retcode is None:
        return OrderState.ERROR
    return OrderState.REJECTED


class JournalError(Exception):
    """The executor's own ledger could not be written: fail closed, do not trade blind."""


class OrderJournal:
    """Reads and writes of the orders, executions, positions and trades tables."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # -- orders ------------------------------------------------------------------------

    def find_order(self, idempotency_key: str) -> OrderSnapshot | None:
        with Session(self._engine) as session:
            row = session.scalars(
                select(OrderRow).where(OrderRow.idempotency_key == idempotency_key)
            ).first()
        return None if row is None else self._snapshot(row)

    def order_by_ticket(self, ticket: int) -> OrderSnapshot | None:
        with Session(self._engine) as session:
            row = session.scalars(
                select(OrderRow).where(OrderRow.broker_order_ticket == ticket)
            ).first()
        return None if row is None else self._snapshot(row)

    def record_request(self, request: OrderRequest, requested_price: Decimal, at: datetime) -> int:
        """Insert the order as SENT, or return the existing row for this key."""
        statement = insert_ignoring_duplicates(self._engine, OrderRow, ("idempotency_key",)).values(
            idempotency_key=request.idempotency_key,
            signal_id=request.signal_id,
            symbol=request.symbol,
            direction=request.direction,
            volume=request.volume,
            requested_price=float(requested_price),
            stop_loss=float(request.stop_loss),
            take_profit=None if request.take_profit is None else float(request.take_profit),
            mode=request.mode,
            state=OrderState.SENT,
            broker_order_ticket=None,
            retcode=None,
            broker_comment=request.comment,
            created_at=at,
            updated_at=at,
        )
        with self._engine.begin() as connection:
            order_id = connection.execute(statement.returning(OrderRow.id)).scalar_one_or_none()
        if order_id is not None:
            return int(order_id)
        existing = self.find_order(request.idempotency_key)
        if existing is None:  # pragma: no cover - the unique constraint makes this impossible
            raise JournalError(f"order {request.idempotency_key} vanished after insert")
        return existing.order_id

    def write_result(
        self,
        order_id: int,
        *,
        state: OrderState,
        ticket: int | None,
        retcode: int | None,
        comment: str,
        at: datetime,
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                update(OrderRow)
                .where(OrderRow.id == order_id)
                .values(
                    state=state,
                    broker_order_ticket=ticket,
                    retcode=retcode,
                    broker_comment=comment[:255],
                    updated_at=at,
                )
            )

    # -- TradeLog: what the executors call ----------------------------------------------

    def record_result(self, order_id: int, result: OrderResult, at: datetime) -> None:
        self.write_result(
            order_id,
            state=order_state_of(result),
            ticket=result.ticket,
            retcode=result.retcode,
            comment=result.message,
            at=at,
        )

    def record_fill(
        self,
        order_id: int,
        request: OrderRequest,
        result: OrderResult,
        *,
        deal_ticket: int,
        at: datetime,
    ) -> int | None:
        """Store the fill and open the local position.

        The signal's move to POSITION_OPEN belongs to the caller: at this instant the order
        is still ORDER_SENT (the fill and the broker's acknowledgement are two steps), and
        RM-018 does not allow ORDER_SENT → POSITION_OPEN. Forcing it here would only log an
        illegal transition on every order.
        """
        if result.ticket is None or result.executed_price is None:
            return None
        self.record_execution(
            order_id,
            deal_ticket=deal_ticket,
            price=float(result.executed_price),
            volume=request.volume,
            slippage=None if result.slippage is None else float(result.slippage),
            at=at,
        )
        position_id = self.open_position(
            order_id,
            BrokerPosition(
                ticket=result.ticket,
                symbol=request.symbol,
                direction=request.direction,
                volume=request.volume,
                open_price=result.executed_price,
                stop_loss=request.stop_loss,
                take_profit=request.take_profit,
                mode=request.mode,
            ),
            at,
        )
        return position_id

    def record_closures(self, closed: Iterable[ClosedPosition], at: datetime) -> tuple[int, ...]:
        """Store each closed trade and move its signal to CLOSED. Idempotent per ticket."""
        trade_ids: list[int] = []
        for item in closed:
            position = self.position_for_ticket(item.ticket)
            if position is None:
                log.warning("closed position %s is not ours: ignored", item.ticket)
                continue
            trade_id = self.close_position(item, mode=position.mode, at=at)
            if trade_id is None:
                continue
            trade_ids.append(trade_id)
            signal_id = item.signal_id if item.signal_id is not None else position.signal_id
            self._move(signal_id, SignalState.CLOSED, at, item.exit_reason)
        return tuple(trade_ids)

    def _move(
        self, signal_id: int | None, target: SignalState, at: datetime, detail: str | None
    ) -> None:
        if signal_id is None:
            return
        try:
            transition(self._engine, signal_id, target, at, detail)
        except IllegalTransitionError as error:
            log.warning("signal %s stayed put: %s", signal_id, error)
        except ValueError as error:
            log.warning("signal %s unknown: %s", signal_id, error)

    def record_execution(
        self,
        order_id: int,
        *,
        deal_ticket: int,
        price: float,
        volume: Decimal,
        slippage: float | None,
        at: datetime,
    ) -> None:
        """Append one fill. The deal ticket is unique, so a replay inserts nothing."""
        with self._engine.begin() as connection:
            connection.execute(
                insert_ignoring_duplicates(
                    self._engine, ExecutionRow, ("broker_deal_ticket",)
                ).values(
                    order_id=order_id,
                    broker_deal_ticket=deal_ticket,
                    price=price,
                    volume=volume,
                    slippage=slippage,
                    executed_at=at,
                )
            )

    # -- positions ---------------------------------------------------------------------

    def open_position(self, order_id: int, position: BrokerPosition, at: datetime) -> int | None:
        statement = insert_ignoring_duplicates(
            self._engine, PositionRow, ("broker_position_ticket",)
        ).values(
            broker_position_ticket=position.ticket,
            order_id=order_id,
            symbol=position.symbol,
            direction=position.direction,
            volume=position.volume,
            open_price=float(position.open_price),
            stop_loss=float(position.stop_loss or ZERO),
            take_profit=None if position.take_profit is None else float(position.take_profit),
            mode=position.mode,
            state=PositionState.OPEN,
            opened_at=at,
            updated_at=at,
        )
        with self._engine.begin() as connection:
            position_id = connection.execute(
                statement.returning(PositionRow.id)
            ).scalar_one_or_none()
        return None if position_id is None else int(position_id)

    def close_position(
        self, closed: ClosedPosition, *, mode: TradingMode, at: datetime
    ) -> int | None:
        """Close the local position and append its trade row. Idempotent per ticket."""
        with Session(self._engine) as session, session.begin():
            row = session.scalars(
                select(PositionRow)
                .where(
                    PositionRow.broker_position_ticket == closed.ticket,
                    PositionRow.mode == mode,
                )
                .with_for_update()
            ).first()
            if row is None or row.state is PositionState.CLOSED:
                return None
            signal_id = session.scalar(
                select(OrderRow.signal_id).where(OrderRow.id == row.order_id)
            )
            risk_eur = self._risk_eur(session, signal_id)
            row.state = PositionState.CLOSED
            row.updated_at = at
            session.flush()
            trade_id = session.execute(
                insert_ignoring_duplicates(self._engine, TradeRow, ("position_id",))
                .values(
                    position_id=row.id,
                    mode=mode,
                    closed_at=closed.closed_at,
                    close_price=float(closed.exit_price),
                    pnl_eur=closed.pnl_eur,
                    risk_eur=risk_eur,
                    exit_reason=closed.exit_reason,
                )
                .returning(TradeRow.id)
            ).scalar_one_or_none()
            return None if trade_id is None else int(trade_id)

    def local_positions(self, mode: TradingMode) -> tuple[LocalPosition, ...]:
        statement = self._position_query().where(
            PositionRow.state == PositionState.OPEN, PositionRow.mode == mode
        )
        with Session(self._engine) as session:
            rows = session.execute(statement).all()
        return tuple(self._local(position, order) for position, order in rows)

    def position_for_ticket(self, ticket: int) -> LocalPosition | None:
        """The tracked position for that broker ticket, open or already closed."""
        statement = self._position_query().where(PositionRow.broker_position_ticket == ticket)
        with Session(self._engine) as session:
            rows = session.execute(statement).all()
        if not rows:
            return None
        position, order = rows[0]
        return self._local(position, order)

    def realized_pnl(self, mode: TradingMode) -> Decimal:
        """Sum of the closed trades of that mode, computed in Python: money columns are
        stored as text on SQLite, so SQL arithmetic on them is meaningless."""
        statement = select(TradeRow.pnl_eur).where(TradeRow.mode == mode)
        with Session(self._engine) as session:
            values = session.scalars(statement).all()
        return sum((Decimal(str(value)) for value in values), ZERO)

    # -- helpers -----------------------------------------------------------------------

    @staticmethod
    def _position_query() -> Select[PositionRow, OrderRow]:
        return (
            select(PositionRow, OrderRow)
            .join(OrderRow, PositionRow.order_id == OrderRow.id)
            .order_by(PositionRow.opened_at)
        )

    @staticmethod
    def _local(position: PositionRow, order: OrderRow) -> LocalPosition:
        return LocalPosition(
            position_id=position.id,
            ticket=position.broker_position_ticket,
            order_id=position.order_id,
            signal_id=order.signal_id,
            symbol=position.symbol,
            direction=position.direction,
            volume=position.volume,
            open_price=Decimal(str(position.open_price)),
            stop_loss=None if not position.stop_loss else Decimal(str(position.stop_loss)),
            take_profit=(
                None if position.take_profit is None else Decimal(str(position.take_profit))
            ),
            mode=position.mode,
        )

    @staticmethod
    def _snapshot(row: OrderRow) -> OrderSnapshot:
        return OrderSnapshot(
            order_id=row.id,
            idempotency_key=row.idempotency_key,
            signal_id=row.signal_id,
            mode=row.mode,
            ticket=row.broker_order_ticket,
            retcode=row.retcode,
            state=row.state.value,
        )

    @staticmethod
    def _risk_eur(session: Session, signal_id: int | None) -> Decimal:
        """The risk the engine authorized for that signal, the denominator of R multiples."""
        if signal_id is None:
            return ZERO
        value = session.scalar(
            select(RiskDecisionRow.risk_eur)
            .where(RiskDecisionRow.signal_id == signal_id)
            .order_by(RiskDecisionRow.id.desc())
            .limit(1)
        )
        return ZERO if value is None else Decimal(str(value))


__all__ = ["JournalError", "OrderJournal"]
