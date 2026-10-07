"""TASK-070 acceptance criteria: paper fills, simulated exits, and above all no order path.

The central criterion — "no execution network call is emitted in PAPER" — is proven with a
terminal whose every trading method raises: the paper broker is wired to `Terminal`, which
has none of them, and the spy proves none was reached.
"""

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import Engine, select
from tests.execution.conftest import NOW, FakeLog, make_signal, request

from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import PositionState
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.terminal import (
    AccountSnapshot,
    Credentials,
    RawBar,
    RawTick,
    SymbolSpec,
    Terminal,
)
from tradingagent.execution.journal import OrderJournal
from tradingagent.execution.paper_broker import PaperBroker
from tradingagent.execution.tracking import STOP_LOSS, TAKE_PROFIT
from tradingagent.risk.model import AccountState, InstrumentSpec
from tradingagent.storage.models import ExecutionRow, PositionRow, TradeRow

GOLD = InstrumentSpec(
    "XAUUSD", Decimal(100), Decimal("0.01"), Decimal("0.01"), Decimal(100), Decimal("0.01"), 10
)


class SpyTerminal:
    """A data terminal that raises on every trading call the paper path must never make."""

    def __init__(self, bid: float = 2400.0, ask: float = 2400.2) -> None:
        self.tick = RawTick(server_epoch=1_790_748_000, bid=bid, ask=ask)
        self.calls: list[str] = []

    def initialize(self, credentials: Credentials) -> None:
        self.calls.append("initialize")

    def shutdown(self) -> None:
        self.calls.append("shutdown")

    def account(self) -> AccountSnapshot:
        raise AssertionError("paper trading must not read the account here")

    def max_bars(self) -> int:
        return 100_000

    def select(self, symbol: str) -> bool:
        self.calls.append("select")
        return True

    def rates(self, symbol: str, timeframe: Timeframe, count: int) -> list[RawBar]:
        self.calls.append("rates")
        return []

    def last_tick(self, symbol: str) -> RawTick | None:
        self.calls.append("last_tick")
        return self.tick

    def is_connected(self) -> bool:
        return True

    # None of these exist on `Terminal`; touching them would be the bug under test.
    def order_check(self, request: object) -> object:
        raise AssertionError("PAPER emitted an order_check")

    def order_send(self, request: object) -> object:
        raise AssertionError("PAPER emitted an order_send")

    def positions(self, symbol: str | None = None) -> tuple[object, ...]:
        raise AssertionError("PAPER queried broker positions")

    def deals_since(self, server_epoch: int) -> tuple[object, ...]:
        raise AssertionError("PAPER queried broker deals")

    def symbol_spec(self, symbol: str) -> SymbolSpec | None:
        raise AssertionError("PAPER queried a broker specification")

    def funds(self) -> object:
        raise AssertionError("PAPER queried broker funds")

    def calc_profit(self, *args: object) -> float | None:
        raise AssertionError("PAPER asked the broker to price a trade")

    def calc_margin(self, *args: object) -> float | None:
        raise AssertionError("PAPER asked the broker for margin")


ACCOUNT = AccountState(
    login=40123456, is_demo=True, currency="EUR", equity=Decimal(1000), free_margin=Decimal(1000)
)


def paper(terminal: Terminal, log: FakeLog, *, account: AccountState = ACCOUNT) -> PaperBroker:
    return PaperBroker(
        terminal,
        log,
        specs={"XAUUSD": GOLD},
        account=account,
        now=lambda: NOW,
    )


def test_no_execution_call_is_ever_emitted_in_paper_mode() -> None:
    terminal, log = SpyTerminal(), FakeLog()
    instance = paper(terminal, log)

    result = asyncio.run(instance.place(request(mode=TradingMode.PAPER, key="paper-1")))
    asyncio.run(instance.on_tick("XAUUSD", Decimal("2399"), Decimal("2399.2")))
    asyncio.run(instance.close(result.ticket or 0, "rule"))

    assert set(terminal.calls) <= {"last_tick"}
    assert terminal.calls  # the flow was really exercised


def test_the_fill_pays_the_observed_spread_and_the_slippage_hypothesis() -> None:
    terminal, log = SpyTerminal(), FakeLog()
    instance = paper(terminal, log)

    result = asyncio.run(instance.place(request(mode=TradingMode.PAPER, key="paper-2")))

    assert result.accepted and result.ticket is not None
    assert result.requested_price == Decimal("2400.2")  # the ask, not the mid
    assert result.executed_price == Decimal("2400.22")  # 2 points of slippage
    assert result.slippage == Decimal("0.02")


def test_the_stop_closes_the_position_with_its_reason_and_the_result() -> None:
    terminal, log = SpyTerminal(), FakeLog()
    instance = paper(terminal, log)
    order = request(mode=TradingMode.PAPER, key="paper-3")
    result = asyncio.run(instance.place(order))

    closed = asyncio.run(instance.on_tick("XAUUSD", Decimal("2389"), Decimal("2389.2")))

    assert len(closed) == 1
    assert closed[0].ticket == result.ticket
    assert closed[0].exit_reason == STOP_LOSS
    assert closed[0].exit_price == Decimal("2390")
    assert closed[0].pnl_eur < 0
    assert log.closure_count == 1
    assert asyncio.run(instance.open_positions()) == ()


def test_the_target_closes_the_position_after_the_stop_is_not_hit() -> None:
    terminal, log = SpyTerminal(), FakeLog()
    instance = paper(terminal, log)
    asyncio.run(instance.place(request(mode=TradingMode.PAPER, key="paper-4")))

    closed = asyncio.run(instance.on_tick("XAUUSD", Decimal("2421"), Decimal("2421.2")))

    assert closed[0].exit_reason == TAKE_PROFIT
    assert closed[0].pnl_eur > 0


def test_a_candle_honours_the_stop_through_the_bar() -> None:
    terminal, log = SpyTerminal(), FakeLog()
    instance = paper(terminal, log)
    asyncio.run(instance.place(request(mode=TradingMode.PAPER, key="paper-5")))
    bar = Candle(Timeframe.M15, NOW, 2400.0, 2405.0, 2385.0, 2402.0)

    closed = asyncio.run(instance.on_candle("XAUUSD", bar))

    assert len(closed) == 1
    assert closed[0].exit_reason == STOP_LOSS


def test_a_rule_exit_closes_at_the_market_with_the_rule_reason() -> None:
    terminal, log = SpyTerminal(), FakeLog()
    instance = paper(terminal, log)
    result = asyncio.run(instance.place(request(mode=TradingMode.PAPER, key="paper-6")))

    outcome = asyncio.run(instance.close(result.ticket or 0, "rule"))

    assert outcome.closed is True
    assert outcome.exit_reason == "rule"
    assert log.closure_count == 1


def test_a_repeated_key_never_opens_a_second_paper_position() -> None:
    terminal, log = SpyTerminal(), FakeLog()
    instance = paper(terminal, log)
    order = request(mode=TradingMode.PAPER, key="paper-7")

    first = asyncio.run(instance.place(order))
    second = asyncio.run(instance.place(order))

    assert first.ticket == second.ticket
    assert len(asyncio.run(instance.open_positions())) == 1


def test_paper_refuses_a_real_money_mode() -> None:
    with pytest.raises(ValueError):
        PaperBroker(
            SpyTerminal(),
            FakeLog(),
            specs={"XAUUSD": GOLD},
            account=ACCOUNT,
            mode=TradingMode.DEMO,
        )


def test_equity_follows_the_realized_result() -> None:
    terminal, log = SpyTerminal(), FakeLog()
    instance = paper(terminal, log)
    asyncio.run(instance.place(request(mode=TradingMode.PAPER, key="paper-8")))
    before = asyncio.run(instance.account())
    terminal.tick = RawTick(server_epoch=1_790_748_060, bid=2421.0, ask=2421.2)
    asyncio.run(instance.on_tick("XAUUSD", Decimal("2421"), Decimal("2421.2")))
    after = asyncio.run(instance.account())

    assert after.equity > before.equity


def test_restart_restores_the_open_positions_from_the_ledger(engine: Engine) -> None:
    """The paper broker never needs the network to resume: the ledger holds its state."""
    from tradingagent.execution.tracking import PositionTracker
    from tradingagent.risk.model import BrokerPosition

    signal_id = make_signal(engine, TradingMode.PAPER)
    journal = OrderJournal(engine)
    order_id = journal.record_request(
        request(mode=TradingMode.PAPER, key="paper-9", signal_id=signal_id),
        Decimal("2400.2"),
        NOW,
    )
    journal.open_position(
        order_id,
        BrokerPosition(
            ticket=777,
            symbol="XAUUSD",
            direction=Direction.BUY,
            volume=Decimal("0.01"),
            open_price=Decimal("2400.2"),
            stop_loss=Decimal("2390"),
            take_profit=None,
            mode=TradingMode.PAPER,
        ),
        NOW,
    )
    instance = PaperBroker(
        SpyTerminal(),
        PositionTracker(engine, now=lambda: NOW),
        specs={"XAUUSD": GOLD},
        account=ACCOUNT,
        now=lambda: NOW,
    )
    asyncio.run(instance.initialize())

    positions = asyncio.run(instance.open_positions())
    assert [p.volume for p in positions] == [Decimal("0.01")]
    assert asyncio.run(instance.reconcile()) == ()


def test_paper_writes_into_the_same_tables_with_mode_paper(engine: Engine) -> None:
    from tradingagent.execution.journal import OrderJournal
    from tradingagent.execution.tracking import PositionTracker

    signal_id = make_signal(engine, TradingMode.PAPER)
    tracker = PositionTracker(engine, now=lambda: NOW)
    instance = PaperBroker(
        SpyTerminal(),
        tracker,
        specs={"XAUUSD": GOLD},
        account=ACCOUNT,
        now=lambda: NOW,
    )
    result = asyncio.run(
        instance.place(request(mode=TradingMode.PAPER, key="paper-db", signal_id=signal_id))
    )
    assert result.ticket is not None
    asyncio.run(instance.on_tick("XAUUSD", Decimal("2389"), Decimal("2389.2")))

    with engine.connect() as connection:
        executions = connection.execute(select(ExecutionRow)).all()
        positions = connection.execute(select(PositionRow)).all()
        trades = connection.execute(select(TradeRow)).all()

    assert len(executions) == 1 and executions[0].volume == Decimal("0.01")
    assert len(positions) == 1
    assert positions[0].mode is TradingMode.PAPER
    assert positions[0].state is PositionState.CLOSED
    assert len(trades) == 1 and trades[0].mode is TradingMode.PAPER
    assert trades[0].exit_reason == STOP_LOSS
    assert OrderJournal(engine).realized_pnl(TradingMode.PAPER) == trades[0].pnl_eur


def test_paper_never_asks_the_broker_for_a_quote_at_startup() -> None:
    """`initialize` restores local state, nothing else."""
    terminal = SpyTerminal()
    instance = paper(terminal, FakeLog())
    asyncio.run(instance.initialize())
    assert terminal.calls == []
