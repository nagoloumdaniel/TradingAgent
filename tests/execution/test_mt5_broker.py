"""TASK-081 acceptance criteria, one test per criterion.

The broker is exercised against `SimulatedTerminal`: the same interface as the MT5 adapter,
with every fault (lost answer, jurisdiction refusal, missing stop, wrong account) a field.
Nothing here goes near the network.
"""

import asyncio
from decimal import Decimal

import pytest
from tests.execution.conftest import FakeLog, request

from tradingagent.core.account import AccountModeMismatchError
from tradingagent.core.mode import TradingMode
from tradingagent.data.terminal import SymbolSpec
from tradingagent.execution.mt5_broker import MT5Broker, key_comment
from tradingagent.execution.ports import OrderRefusedError
from tradingagent.execution.simulator import RETCODE_BLOCKED, SimulatedTerminal
from tradingagent.execution.tracking import STOP_MISSING
from tradingagent.risk.model import OrderResult

NOW_LOGIN = 40123456


def broker(terminal: SimulatedTerminal, log: FakeLog, **kwargs: object) -> MT5Broker:
    return MT5Broker(
        terminal,
        log,
        login=terminal.login,
        mode=TradingMode.DEMO,
        **kwargs,  # type: ignore[arg-type]
    )


def demo_terminal() -> SimulatedTerminal:
    terminal = SimulatedTerminal()
    terminal.set_tick("XAUUSD", bid=2400.0, ask=2400.2)
    return terminal


def test_comment_is_short_stable_and_keyed_by_the_whole_key() -> None:
    first = key_comment("signal-42:XAUUSD")
    assert first == key_comment("signal-42:XAUUSD")
    assert first != key_comment("signal-43:XAUUSD")
    assert len(first) <= 31
    assert "XAUUSD" not in first  # nothing readable leaks into the broker's comment


def test_stop_is_confirmed_present_read_back_from_the_position() -> None:
    terminal, log = demo_terminal(), FakeLog()
    result = asyncio.run(broker(terminal, log).place(request()))

    assert result.accepted
    assert result.ticket is not None
    assert result.stop_present is True
    positions = terminal.positions()
    assert len(positions) == 1
    assert positions[0].stop_loss == pytest.approx(2390.0)
    assert len(log.fills) == 1


def test_absent_stop_closes_the_position_and_alerts() -> None:
    terminal, log = demo_terminal(), FakeLog()
    terminal.drop_protection = True
    alerts: list[str] = []

    result = asyncio.run(broker(terminal, log, alert=alerts.append).place(request()))

    assert result.accepted
    assert result.stop_present is False
    assert "stop-loss absent" in result.message
    assert terminal.position_tickets() == ()  # closed immediately
    assert alerts and "without its stop-loss" in alerts[0]
    # The close is journalled as a closure, with the stop_missing reason.
    assert log.closure_count == 1


def test_lost_answer_is_recovered_by_comment_without_any_second_order() -> None:
    terminal, log = demo_terminal(), FakeLog()
    terminal.lost_answers = 1

    result = asyncio.run(broker(terminal, log).place(request()))

    assert result.accepted
    assert result.ticket is not None
    assert "no second order sent" in result.message
    assert terminal.calls.count("order_send") == 1


def test_the_same_key_never_sends_twice() -> None:
    terminal, log = demo_terminal(), FakeLog()
    instance = broker(terminal, log)

    first = asyncio.run(instance.place(request()))
    second = asyncio.run(instance.place(request()))

    assert first.ticket == second.ticket
    assert terminal.calls.count("order_send") == 1
    assert log.sent == 1


def test_order_check_is_not_authoritative_a_server_refusal_is() -> None:
    """Measured on the real account: order_check approves what the server refuses."""
    terminal, log = demo_terminal(), FakeLog()
    terminal.check_retcode = 10009  # favourable pre-check
    terminal.reject_retcode = RETCODE_BLOCKED  # "instruments blocked in France"

    result = asyncio.run(broker(terminal, log).place(request()))

    assert not result.accepted
    assert result.retcode == RETCODE_BLOCKED
    assert terminal.position_tickets() == ()
    assert log.fills == []


def test_account_incoherence_stops_the_component() -> None:
    terminal, log = demo_terminal(), FakeLog()
    terminal.is_demo = False  # a real account while the mode is DEMO (RM-017)
    alarms: list[AccountModeMismatchError] = []

    with pytest.raises(AccountModeMismatchError):
        asyncio.run(broker(terminal, log, guardian_alarm=alarms.append).place(request()))

    assert alarms
    assert terminal.calls.count("order_send") == 0
    assert "order_send" not in terminal.calls


def test_expectations_are_checked_before_every_order() -> None:
    terminal, log = demo_terminal(), FakeLog()
    instance = broker(terminal, log)
    asyncio.run(instance.place(request()))
    terminal.is_demo = False  # the account changes under the agent

    with pytest.raises(AccountModeMismatchError):
        asyncio.run(instance.place(request(key="key-2")))

    assert terminal.calls.count("order_send") == 1


def test_requested_executed_and_slippage_are_recorded() -> None:
    terminal, log = demo_terminal(), FakeLog()
    terminal.fill_slippage = 0.3

    result = asyncio.run(broker(terminal, log).place(request()))

    assert result.requested_price == Decimal("2400.2")
    assert result.executed_price == Decimal("2400.5")
    assert result.slippage == Decimal("0.3")


def test_a_halted_agent_sends_nothing() -> None:
    from tradingagent.core.halt import HaltStatus

    terminal, log = demo_terminal(), FakeLog()
    instance = broker(terminal, log, status=lambda: HaltStatus(True, False, ("daily loss limit",)))

    with pytest.raises(OrderRefusedError):
        asyncio.run(instance.place(request()))

    assert terminal.calls.count("order_send") == 0


def test_paper_mode_must_not_use_the_terminal() -> None:
    with pytest.raises(ValueError, match="PaperBroker"):
        MT5Broker(demo_terminal(), FakeLog(), login=NOW_LOGIN, mode=TradingMode.PAPER)


def test_an_order_carrying_another_mode_is_refused() -> None:
    terminal, log = demo_terminal(), FakeLog()

    with pytest.raises(OrderRefusedError):
        asyncio.run(broker(terminal, log).place(request(key="p", mode=TradingMode.PAPER)))

    assert terminal.calls.count("order_send") == 0


def test_quote_derives_the_eur_loss_and_margin_from_the_terminal() -> None:
    terminal, log = demo_terminal(), FakeLog()
    instance = broker(terminal, log)

    quote = asyncio.run(instance.quote("XAUUSD", request().direction, Decimal("2390")))

    assert quote.bid == Decimal("2400.0")
    assert quote.ask == Decimal("2400.2")
    # contract size 100 * 10.2 distance = 1020 in the account currency (broker float maths)
    assert quote.loss_one_lot is not None
    assert abs(quote.loss_one_lot - Decimal("1020")) < Decimal("0.01")
    assert quote.margin_one_lot is not None and quote.margin_one_lot > 0
    assert quote.profit_to_eur is not None


def test_missing_specification_refuses_the_order() -> None:
    terminal, log = demo_terminal(), FakeLog()
    terminal.specs.pop("XAUUSD")
    instance = broker(terminal, log)

    with pytest.raises(Exception, match="specification"):
        asyncio.run(instance.instrument("XAUUSD"))


def test_instrument_is_converted_to_exact_decimals() -> None:
    terminal, log = demo_terminal(), FakeLog()
    terminal.specs["XAUUSD"] = SymbolSpec("XAUUSD", 100.0, 0.01, 0.01, 100.0, 0.01, 10, 3)

    spec = asyncio.run(broker(terminal, log).instrument("XAUUSD"))

    assert spec.contract_size == Decimal("100.0")
    assert spec.volume_min == Decimal("0.01")
    assert spec.stops_level == 10


def test_close_reports_the_net_result_and_leaves_nothing_open() -> None:
    terminal, log = demo_terminal(), FakeLog()
    instance = broker(terminal, log)
    opened = asyncio.run(instance.place(request()))
    assert opened.ticket is not None

    result = asyncio.run(instance.close(opened.ticket, STOP_MISSING))

    assert result.closed is True
    assert result.pnl_eur is not None
    assert terminal.position_tickets() == ()
    assert log.closure_count == 1


def test_broker_closures_are_detected_from_the_deals() -> None:
    terminal, log = demo_terminal(), FakeLog()
    instance = broker(terminal, log)
    opened = asyncio.run(instance.place(request()))
    assert opened.ticket is not None
    # A stop triggers at the broker between two cycles: the position disappears, a deal appears.
    terminal_order_send_close(terminal, opened.ticket)

    closed = asyncio.run(instance.on_tick("XAUUSD", Decimal("2390"), Decimal("2390.2")))

    assert len(closed) == 1
    assert closed[0].ticket == opened.ticket
    assert closed[0].exit_reason in {"stop_loss", "broker"}
    assert log.closure_count == 1


def terminal_order_send_close(terminal: SimulatedTerminal, ticket: int) -> None:
    from tradingagent.core.market import Direction
    from tradingagent.data.terminal import TradeRequest

    terminal.order_send(
        TradeRequest(
            symbol="XAUUSD",
            direction=Direction.SELL,
            volume=0.01,
            price=2390.0,
            position_ticket=ticket,
            comment="stop out",
        )
    )


def test_replay_of_a_refused_order_does_not_retry() -> None:
    terminal, log = demo_terminal(), FakeLog()
    terminal.reject_retcode = RETCODE_BLOCKED
    instance = broker(terminal, log)

    first = asyncio.run(instance.place(request()))
    second = asyncio.run(instance.place(request()))

    assert not first.accepted and not second.accepted
    assert terminal.calls.count("order_send") == 1


def test_result_dataclass_is_the_shared_risk_model_one() -> None:
    terminal, log = demo_terminal(), FakeLog()
    result = asyncio.run(broker(terminal, log).place(request()))
    assert isinstance(result, OrderResult)
