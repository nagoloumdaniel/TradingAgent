"""What `order_send` must survive: a silent terminal, and a nonsensical answer.

`mt5.order_send` answers `None` when the terminal does not reply, and `mt5.last_error()`
then holds the only reason there is. On 2026-10-08 the agent lost four orders to that
silence and could not say why: `order_check` kept its error, `order_send` threw its own
away and the journal recorded "answer lost" with no code. These tests hold the reason in
place, and are unit tests on purpose — the terminal is not needed to send nothing back.
"""

from unittest.mock import MagicMock

import pytest

import tradingagent.data.mt5_terminal as terminal_module
from tradingagent.core.market import Direction
from tradingagent.data.mt5_terminal import Mt5Terminal
from tradingagent.data.terminal import TerminalError, TradeRequest

#: A real MT5 refusal: the server asked for a new price before the order was taken.
NO_REPLY = (10004, "Requote")


def any_order_request() -> TradeRequest:
    return TradeRequest(
        symbol="XAUUSD",
        direction=Direction.BUY,
        volume=0.01,
        price=4116.10,
        stop_loss=4100.00,
        take_profit=4140.00,
        comment="ta-test",
    )


def test_a_silent_terminal_names_the_reason_instead_of_an_empty_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`None` means "no reply", so the code and the text of `last_error` must come back."""
    monkeypatch.setattr(terminal_module.mt5, "order_send", lambda _fields: None)
    monkeypatch.setattr(terminal_module.mt5, "last_error", lambda: NO_REPLY)

    with pytest.raises(TerminalError) as caught:
        Mt5Terminal().order_send(any_order_request())

    message = str(caught.value)
    assert str(NO_REPLY[0]) in message
    assert NO_REPLY[1] in message
    assert "order_send" in message


def test_the_refusal_stays_an_error_even_when_the_reason_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal that fails without a code still must not look like a successful call."""
    monkeypatch.setattr(terminal_module.mt5, "order_send", lambda _fields: None)
    monkeypatch.setattr(terminal_module.mt5, "last_error", lambda: (0, ""))

    with pytest.raises(TerminalError) as caught:
        Mt5Terminal().order_send(any_order_request())

    assert "order_send" in str(caught.value)


def test_a_malformed_reply_is_a_terminal_error_not_an_attribute_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The counterpart of the silence: a reply missing its fields is refused, not trusted.

    The broker catches `TerminalError` at the call site; an `AttributeError` raised while
    reading a half-built reply would escape the executor and take the cycle with it.
    """
    monkeypatch.setattr(terminal_module.mt5, "order_send", MagicMock(return_value=object()))

    with pytest.raises(TerminalError, match="order_send"):
        Mt5Terminal().order_send(any_order_request())
