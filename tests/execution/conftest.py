"""Fixtures shared by the executor tests: a migrated database and an in-memory journal."""

import os
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import SignalState
from tradingagent.core.timeframe import Timeframe
from tradingagent.execution.ports import LocalPosition, OrderSnapshot
from tradingagent.risk.model import ClosedPosition, OrderRequest, OrderResult
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.signals import SignalRecord, SignalRepository, idempotency_key, transition
from tradingagent.strategies.manifest import StrategyManifest

# Matches tests/storage/conftest.py: a real server is opt-in and every test drops all tables.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
MANIFEST = StrategyManifest(
    strategy_id="witness",
    version="1.0.0",
    max_mode=TradingMode.SIGNAL,
    allowed_symbols=("XAUUSD",),
    timeframes=(Timeframe.M15,),
    history_bars=20,
)


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    if TEST_DATABASE_URL:
        return TEST_DATABASE_URL
    return f"sqlite:///{tmp_path / 'execution.db'}"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    upgrade(database_url)
    built = create_database_engine(database_url)
    yield built
    built.dispose()


def make_signal(engine: Engine, mode: TradingMode = TradingMode.DEMO, at: datetime = NOW) -> int:
    """A real signal row, needed by the orders' foreign key."""
    record = SignalRecord(
        idempotency_key=idempotency_key(MANIFEST.ref, "XAUUSD", Timeframe.M15, at),
        manifest=MANIFEST,
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        direction=Direction.BUY,
        mode=mode,
        observed_price=2400.5,
        entry_low=2400.0,
        entry_high=2401.0,
        stop_loss=2390.0,
        take_profits=(2410.0, 2420.0),
        reason="test signal",
        indicators={"sma_20": 2395.25},
        generated_at=at,
        expires_at=at + timedelta(minutes=15),
    )
    signal_id = SignalRepository(engine).record(record)
    assert signal_id is not None
    return signal_id


def accept_signal(engine: Engine, signal_id: int) -> None:
    """Walk the signal to ORDER_ACCEPTED, the state an execution follows."""
    for state in (
        SignalState.VALIDATED,
        SignalState.SENT,
        SignalState.ACCEPTED,
        SignalState.ORDER_SENT,
        SignalState.ORDER_ACCEPTED,
    ):
        transition(engine, signal_id, state, NOW)


def request(
    key: str = "key-1", mode: TradingMode = TradingMode.DEMO, **changes: object
) -> OrderRequest:
    base = OrderRequest(
        signal_id=1,
        idempotency_key=key,
        symbol="XAUUSD",
        direction=Direction.BUY,
        volume=Decimal("0.01"),
        stop_loss=Decimal("2390"),
        take_profit=Decimal("2420"),
        mode=mode,
        comment="ta",
    )
    return replace(base, **changes)  # type: ignore[arg-type]


@dataclass
class FakeLog:
    """An in-memory `TradeLog`: records exactly what the executor asks it to store."""

    orders: dict[str, OrderSnapshot] = field(default_factory=dict)
    results: list[tuple[int, OrderResult]] = field(default_factory=list)
    fills: list[tuple[int, str, int]] = field(default_factory=list)
    positions: dict[int, LocalPosition] = field(default_factory=dict)
    trades: list[tuple[int, str, Decimal]] = field(default_factory=list)
    next_order_id: int = 1

    def find_order(self, idempotency_key: str) -> OrderSnapshot | None:
        return self.orders.get(idempotency_key)

    def record_request(self, request: OrderRequest, requested_price: Decimal, at: datetime) -> int:
        existing = self.orders.get(request.idempotency_key)
        if existing is not None:
            return existing.order_id
        order_id = self.next_order_id
        self.next_order_id += 1
        self.orders[request.idempotency_key] = OrderSnapshot(
            order_id=order_id,
            idempotency_key=request.idempotency_key,
            signal_id=request.signal_id,
            mode=request.mode,
            ticket=None,
            retcode=None,
            state="sent",
        )
        return order_id

    def record_result(self, order_id: int, result: OrderResult, at: datetime) -> None:
        self.results.append((order_id, result))
        for key, snapshot in self.orders.items():
            if snapshot.order_id == order_id:
                self.orders[key] = replace(
                    snapshot,
                    ticket=result.ticket,
                    retcode=result.retcode,
                    state="accepted" if result.accepted else "rejected",
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
        if result.ticket is None or result.executed_price is None:
            return None
        self.fills.append((order_id, request.idempotency_key, deal_ticket))
        self.positions[result.ticket] = LocalPosition(
            position_id=len(self.positions) + 1,
            ticket=result.ticket,
            order_id=order_id,
            signal_id=request.signal_id,
            symbol=request.symbol,
            direction=request.direction,
            volume=request.volume,
            open_price=result.executed_price,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            mode=request.mode,
        )
        return len(self.positions)

    def record_closures(self, closed: Iterable[ClosedPosition], at: datetime) -> tuple[int, ...]:
        trade_ids: list[int] = []
        for item in closed:
            position: LocalPosition | None = self.positions.pop(item.ticket, None)
            if position is None:
                continue
            self.trades.append((item.ticket, item.exit_reason, item.pnl_eur))
            trade_ids.append(len(self.trades))
        return tuple(trade_ids)

    def local_positions(self, mode: TradingMode) -> tuple[LocalPosition, ...]:
        return tuple(p for p in self.positions.values() if p.mode is mode)

    def position_for_ticket(self, ticket: int) -> LocalPosition | None:
        return self.positions.get(ticket)

    def realized_pnl(self, mode: TradingMode) -> Decimal:
        return sum((pnl for _, _, pnl in self.trades), Decimal(0))

    @property
    def sent(self) -> int:
        return len(self.orders)

    @property
    def closure_count(self) -> int:
        return len(self.trades)


@pytest.fixture
def log() -> FakeLog:
    return FakeLog()


@pytest.fixture
def signal_id(engine: Engine) -> int:
    return make_signal(engine)


@pytest.fixture
def now() -> Callable[[], datetime]:
    return lambda: NOW


def closed(ticket: int = 1000, **changes: object) -> ClosedPosition:
    base = ClosedPosition(
        ticket=ticket,
        symbol="XAUUSD",
        exit_price=Decimal("2390"),
        pnl_eur=Decimal("-10.5"),
        exit_reason="stop_loss",
        closed_at=NOW,
        signal_id=None,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


__all__ = [
    "MANIFEST",
    "NOW",
    "FakeLog",
    "accept_signal",
    "closed",
    "make_signal",
    "request",
]
