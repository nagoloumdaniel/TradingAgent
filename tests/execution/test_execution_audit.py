"""Adversarial audit of the DEMO execution path (task-2, 2026-10-08).

Seven leads, each answered by a test rather than by an opinion:

1. idempotence      — a lost answer is never resent, even when its position already closed;
2. `stop_present`   — the read-back survives a terminal whose position cache lags the fill;
3. broker closures  — a stop hit at the broker is collected, journalled and told, once;
4. reconciliation   — a broker-side stop-out is not a state divergence (RM-014);
5. EUR conversion   — an absent rate is refused by the sizer, never assumed silently;
6. lot step         — a volume that breaks the step or the limits never reaches the terminal;
7. terminal errors  — a server refusal is journalled with its code.

Some tests characterise behaviour that is already right, or wrong elsewhere and reported
rather than fixed: they are the evidence of the audit, not a to-do list.
"""

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session
from tests.execution.conftest import NOW, FakeLog, accept_signal, make_signal, request

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import OrderState, SignalState
from tradingagent.data.terminal import PositionInfo, TradeRequest, TradeResult
from tradingagent.execution.mt5_broker import MT5Broker
from tradingagent.execution.simulator import RETCODE_BLOCKED, SimulatedLostAnswer, SimulatedTerminal
from tradingagent.execution.tracking import PositionTracker
from tradingagent.risk.model import InstrumentSpec, MarketQuote
from tradingagent.risk.sizing import SizingError, size_position
from tradingagent.storage.models import ExecutionRow, OrderRow, PositionRow, TradeRow
from tradingagent.storage.signals import transition

KEY = "audit:XAUUSD:2026-10-06T12:00Z"
KILL_PRICE = 2390.0  # the stop of the audit order: where the broker stops it out


def demo_terminal() -> SimulatedTerminal:
    terminal = SimulatedTerminal()
    terminal.set_tick("XAUUSD", bid=2400.0, ask=2400.2)
    terminal.set_tick("BTCUSD", bid=60000.0, ask=60010.0)
    return terminal


def broker(terminal: SimulatedTerminal, log: object, **kwargs: object) -> MT5Broker:
    return MT5Broker(
        terminal,
        log,  # type: ignore[arg-type]
        login=terminal.login,
        mode=TradingMode.DEMO,
        now=lambda: NOW,
        **kwargs,  # type: ignore[arg-type]
    )


def stop_out(terminal: SimulatedTerminal, ticket: int, price: float = KILL_PRICE) -> None:
    """The stop is hit at the broker: the position vanishes, a closing deal appears."""
    sold = next(item for item in terminal.positions() if item.ticket == ticket)
    terminal.order_send(
        TradeRequest(
            symbol=sold.symbol,
            direction=Direction.SELL if sold.direction is Direction.BUY else Direction.BUY,
            volume=sold.volume,
            price=price,
            position_ticket=ticket,
            comment="sl",
        )
    )


def trades(engine: Engine) -> list[TradeRow]:
    with Session(engine) as session:
        return list(session.scalars(select(TradeRow)).all())


def open_position(
    engine: Engine,
    instance: MT5Broker,
    *,
    key: str = KEY,
    at: object = NOW,
    symbol: str = "XAUUSD",
    stop: Decimal = Decimal("2390"),
    volume: Decimal = Decimal("0.01"),
) -> int:
    """One accepted order and its open position in the ledger, with a real signal row."""
    signal_id = make_signal(engine, at=at)  # type: ignore[arg-type]
    accept_signal(engine, signal_id)
    placed = asyncio.run(
        instance.place(
            request(
                key=key,
                signal_id=signal_id,
                symbol=symbol,
                stop_loss=stop,
                take_profit=None,
                volume=volume,
            )
        )
    )
    assert placed.accepted and placed.ticket is not None
    transition(engine, signal_id, SignalState.POSITION_OPEN, NOW)
    return placed.ticket


# -- 1. idempotence --------------------------------------------------------------------


class ClosedOnLostAnswer(SimulatedTerminal):
    """The answer is lost *and* the stop is hit before anyone can reconcile.

    The stop-out is staged on the way out: the position is gone by the time the broker
    looks for it by comment, which is exactly the race this test is about.
    """

    def order_send(self, request: TradeRequest) -> TradeResult:
        try:
            return super().order_send(request)
        except SimulatedLostAnswer:
            if request.position_ticket is None:
                tickets = self.position_tickets()
                if tickets:
                    stop_out(self, max(tickets))
            raise


def test_a_lost_answer_whose_position_already_closed_is_never_resent() -> None:
    """The key survives an answer lost *and* a stop hit: an explicit refusal, no resend."""
    terminal = ClosedOnLostAnswer()
    terminal.set_tick("XAUUSD", bid=2400.0, ask=2400.2)
    terminal.lost_answers = 1
    log = FakeLog()
    instance = broker(terminal, log)

    first = asyncio.run(instance.place(request(key=KEY)))
    second = asyncio.run(instance.place(request(key=KEY)))

    assert first.accepted is False and first.retcode is None
    assert second.accepted is False and second.retcode is None
    # Two sends: the order, then the stop-out the broker executed on its own. Never a third.
    assert terminal.calls.count("order_send") == 2
    assert terminal.position_tickets() == ()


def test_a_lost_answer_is_recovered_by_comment_and_journalled(engine: Engine) -> None:
    """The nominal recovery: the ticket is adopted, the order row is FILLED, nothing resent."""
    terminal = demo_terminal()
    terminal.lost_answers = 1
    tracker = PositionTracker(engine, now=lambda: NOW)
    instance = broker(terminal, tracker)
    signal_id = make_signal(engine)
    accept_signal(engine, signal_id)

    result = asyncio.run(instance.place(request(key=KEY, signal_id=signal_id)))

    assert result.accepted and result.ticket is not None
    assert terminal.calls.count("order_send") == 1
    with engine.connect() as connection:
        row = connection.execute(select(OrderRow).where(OrderRow.idempotency_key == KEY)).one()
    assert row.state is OrderState.FILLED
    assert row.broker_order_ticket == result.ticket


def test_a_recovered_fill_carries_a_real_deal_ticket(engine: Engine) -> None:
    """`executions.broker_deal_ticket` is a deal ticket, never the position ticket."""
    terminal = demo_terminal()
    terminal.lost_answers = 1
    tracker = PositionTracker(engine, now=lambda: NOW)
    instance = broker(terminal, tracker)
    signal_id = make_signal(engine)
    accept_signal(engine, signal_id)

    result = asyncio.run(instance.place(request(key=KEY, signal_id=signal_id)))
    assert result.ticket is not None

    with engine.connect() as connection:
        execution = connection.execute(select(ExecutionRow)).one()
    deals = {deal.ticket: deal for deal in terminal.deals_since(0)}
    assert execution.broker_deal_ticket in deals, (
        f"{execution.broker_deal_ticket} is not a deal ticket (deals: {sorted(deals)})"
    )
    entry = deals[execution.broker_deal_ticket]
    assert entry.is_entry and entry.position_ticket == result.ticket


# -- 2. stop_present read-back ---------------------------------------------------------


class LaggingTerminal(SimulatedTerminal):
    """The terminal's position cache has not caught up with the fill yet."""

    def __init__(self, hidden_reads: int = 1) -> None:
        super().__init__()
        self.hidden_reads = hidden_reads

    def positions(self, symbol: str | None = None) -> tuple[PositionInfo, ...]:
        if self.hidden_reads > 0:
            self.hidden_reads -= 1
            return ()
        return super().positions(symbol)


def test_a_lagging_position_cache_does_not_close_a_protected_position() -> None:
    """One stale read must not trigger the EF-015 close of a position that has its stop."""
    terminal = LaggingTerminal()
    terminal.set_tick("XAUUSD", bid=2400.0, ask=2400.2)
    log = FakeLog()
    alerts: list[str] = []

    result = asyncio.run(
        broker(terminal, log, alert=alerts.append, stop_readback_pause=0.0).place(request(key=KEY))
    )

    assert result.accepted and result.stop_present is True
    assert terminal.position_tickets() != ()  # still open, still protected
    assert log.closure_count == 0
    assert alerts == []


def test_a_stop_that_really_is_absent_still_closes_the_position() -> None:
    """The bounded retries never turn a genuinely unprotected position into a kept one."""
    terminal = demo_terminal()
    terminal.drop_protection = True
    alerts: list[str] = []

    result = asyncio.run(
        broker(terminal, FakeLog(), alert=alerts.append, stop_readback_pause=0.0).place(
            request(key=KEY)
        )
    )

    assert result.stop_present is False
    assert terminal.position_tickets() == ()
    assert alerts and "without its stop-loss" in alerts[0]


# -- 3. positions closed by the broker -------------------------------------------------


def test_a_broker_stop_out_is_journalled_once_and_the_operator_is_told(engine: Engine) -> None:
    terminal = demo_terminal()
    tracker = PositionTracker(engine, now=lambda: NOW)
    alerts: list[str] = []
    instance = broker(terminal, tracker, alert=alerts.append)
    ticket = open_position(engine, instance)
    stop_out(terminal, ticket)

    asyncio.run(instance.reconcile())

    rows = trades(engine)
    assert len(rows) == 1
    assert rows[0].exit_reason == "stop_loss"
    assert float(rows[0].pnl_eur) == pytest.approx(-10.2, abs=0.01)
    assert tracker.local_positions(TradingMode.DEMO) == ()
    # The loop only notifies what `on_candle` returns, so the collection that saves the
    # halt has to tell the operator here — once.
    assert alerts and str(ticket) in alerts[0]
    assert "stop_loss" in alerts[0]

    asyncio.run(instance.reconcile())  # the next cycle duplicates nothing
    assert len(trades(engine)) == 1
    assert len(alerts) == 1


def test_three_positions_closed_at_three_different_seconds_are_all_collected(
    engine: Engine,
) -> None:
    """One read for the whole batch: a closure the cursor jumped over is lost for good."""
    terminal = demo_terminal()
    tracker = PositionTracker(engine, now=lambda: NOW)
    instance = broker(terminal, tracker)
    tickets = [
        open_position(
            engine, instance, key=f"{KEY}:{index}", at=NOW + timedelta(minutes=15 * index)
        )
        for index in range(3)
    ]
    for ticket in tickets:
        stop_out(terminal, ticket)
        terminal.clock_epoch += 5  # three stop-outs, three different broker seconds

    closed = asyncio.run(instance.on_tick("XAUUSD", Decimal("2390"), Decimal("2390.2")))

    assert {item.ticket for item in closed} == set(tickets)
    assert len(trades(engine)) == 3
    assert asyncio.run(instance.on_tick("XAUUSD", Decimal("2390"), Decimal("2390.2"))) == ()
    assert len(trades(engine)) == 3


# -- 4. reconciliation ------------------------------------------------------------------


def test_a_broker_stop_out_is_not_a_state_divergence(engine: Engine) -> None:
    """RM-014 guards against unexplained gaps, not against a stop that did its job."""
    terminal = demo_terminal()
    tracker = PositionTracker(engine, now=lambda: NOW)
    instance = broker(terminal, tracker)
    ticket = open_position(engine, instance)
    stop_out(terminal, ticket)

    divergences = asyncio.run(instance.reconcile())

    assert divergences == ()


def test_a_broker_position_the_ledger_never_saw_is_still_a_divergence(engine: Engine) -> None:
    """The other direction is untouched: a real gap still has to halt the agent (RM-014)."""
    terminal = demo_terminal()
    tracker = PositionTracker(engine, now=lambda: NOW)
    instance = broker(terminal, tracker)
    ticket = open_position(engine, instance)
    with engine.begin() as connection:
        connection.execute(delete(PositionRow).where(PositionRow.broker_position_ticket == ticket))

    divergences = asyncio.run(instance.reconcile())

    assert len(divergences) == 1
    assert "exists at the broker but not locally" in divergences[0]


# -- 5. EUR conversion ------------------------------------------------------------------


class NoConversion(SimulatedTerminal):
    def calc_profit(
        self,
        direction: Direction,
        symbol: str,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> float | None:
        return None


def _gold_spec() -> InstrumentSpec:
    return InstrumentSpec(
        symbol="XAUUSD",
        contract_size=Decimal(100),
        volume_min=Decimal("0.01"),
        volume_step=Decimal("0.01"),
        volume_max=Decimal(100),
        point=Decimal("0.01"),
        stops_level=10,
    )


def _size(quote: MarketQuote) -> object:
    return size_position(
        capital=Decimal(1000),
        risk_per_trade=Decimal("0.005"),
        stop_distance=Decimal("10.2"),
        spec=_gold_spec(),
        quote=quote,
        free_margin=Decimal(1000),
        margin_usage=Decimal("0.5"),
        max_volume=None,
    )


def test_an_absent_conversion_rate_is_refused_by_the_sizer() -> None:
    """No rate means no size: the sizer refuses instead of assuming one."""
    terminal = NoConversion()
    terminal.set_tick("XAUUSD", bid=2400.0, ask=2400.2)
    instance = broker(terminal, FakeLog())

    quote = asyncio.run(instance.quote("XAUUSD", Direction.BUY, Decimal("2390")))

    assert quote.loss_one_lot is None and quote.profit_to_eur is None
    with pytest.raises(SizingError, match="unavailable"):
        _size(quote)


def test_a_quote_without_a_stop_carries_no_rate_at_all() -> None:
    """Why `runtime/pipeline._open_exposure` always falls back to `rate = 1` (line 432)."""
    instance = broker(demo_terminal(), FakeLog())

    quote = asyncio.run(instance.quote("XAUUSD", Direction.BUY, None))

    assert quote.loss_one_lot is None
    assert quote.profit_to_eur is None


def test_the_loss_cross_check_cannot_fail_because_the_rate_comes_from_the_loss() -> None:
    """A tenfold rate error is accepted: `size_position` compares the figure to itself."""

    class AbsurdConversion(SimulatedTerminal):
        def calc_profit(
            self,
            direction: Direction,
            symbol: str,
            volume: float,
            price_open: float,
            price_close: float,
        ) -> float | None:
            base = super().calc_profit(direction, symbol, volume, price_open, price_close)
            return None if base is None else base * 10

    terminal = AbsurdConversion()
    terminal.set_tick("XAUUSD", bid=2400.0, ask=2400.2)
    instance = broker(terminal, FakeLog())

    quote = asyncio.run(instance.quote("XAUUSD", Direction.BUY, Decimal("2390")))

    assert quote.profit_to_eur is not None  # tenfold the real conversion, and accepted
    assert float(quote.profit_to_eur) == pytest.approx(10.0, abs=0.01)
    with pytest.raises(SizingError) as refusal:
        _size(quote)
    # Refused on the minimum lot, never on the divergence the cross-check exists to catch.
    assert "below the minimum lot" in str(refusal.value)
    assert "diverge" not in str(refusal.value)


# -- 6. lot step, minimum and maximum volume --------------------------------------------


@pytest.mark.parametrize(
    ("volume", "expected"),
    [
        (Decimal("0.015"), "lot step"),
        (Decimal("0.001"), "broker limits"),
        (Decimal("500"), "broker limits"),
    ],
)
def test_an_illegal_volume_never_reaches_the_terminal(volume: Decimal, expected: str) -> None:
    terminal, log = demo_terminal(), FakeLog()

    result = asyncio.run(broker(terminal, log).place(request(key=KEY, volume=volume)))

    assert not result.accepted
    assert expected in result.message
    assert str(volume) in result.message
    assert terminal.calls.count("order_send") == 0
    assert log.sent == 0
    assert log.positions == {}


def test_a_legal_volume_is_still_sent() -> None:
    terminal, log = demo_terminal(), FakeLog()

    result = asyncio.run(broker(terminal, log).place(request(key=KEY, volume=Decimal("0.01"))))

    assert result.accepted
    assert terminal.calls.count("order_send") == 1


# -- 7. terminal errors -----------------------------------------------------------------


def test_a_server_refusal_is_journalled_with_its_retcode(engine: Engine) -> None:
    terminal = demo_terminal()
    terminal.reject_retcode = RETCODE_BLOCKED
    tracker = PositionTracker(engine, now=lambda: NOW)
    instance = broker(terminal, tracker)
    signal_id = make_signal(engine)
    accept_signal(engine, signal_id)

    result = asyncio.run(instance.place(request(key=KEY, signal_id=signal_id)))

    assert not result.accepted and result.retcode == RETCODE_BLOCKED
    with engine.connect() as connection:
        row = connection.execute(select(OrderRow).where(OrderRow.idempotency_key == KEY)).one()
    assert row.state is OrderState.REJECTED
    assert row.retcode == RETCODE_BLOCKED
    assert str(RETCODE_BLOCKED) in str(row.broker_comment)
    assert row.broker_order_ticket is None
    assert terminal.position_tickets() == ()
