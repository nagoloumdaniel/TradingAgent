"""The operator's compact trade messages (cahier v3, §37)."""

import dataclasses
from decimal import Decimal

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.notify.trade_messages import (
    PositionClosed,
    PositionOpened,
    money,
    render_position_closed,
    render_position_opened,
    signed_money,
)

OPENED = PositionOpened(
    symbol="BTCUSD",
    direction=Direction.BUY,
    volume=Decimal("0.01"),
    entry_price=Decimal("84010.5"),
    stop_loss=Decimal("83900"),
    take_profit=Decimal("84500"),
    strategy_ref="trend_breakout@1.0.0",
    mode=TradingMode.DEMO,
    ticket=555001,
)

CLOSED = PositionClosed(
    symbol="BTCUSD",
    pnl=Decimal("10.00"),
    balance=Decimal("1010.00"),
    currency="USD",
    exit_reason="take_profit",
    ticket=555001,
)


def opened(**changes: object) -> PositionOpened:
    return dataclasses.replace(OPENED, **changes)  # type: ignore[arg-type]


def closed(**changes: object) -> PositionClosed:
    return dataclasses.replace(CLOSED, **changes)  # type: ignore[arg-type]


def test_the_opened_message_is_short_and_carries_the_position_essentials() -> None:
    text = render_position_opened(opened())
    lines = text.splitlines()
    assert len(lines) == 4
    assert "BTCUSD" in lines[0]
    assert "ACHAT" in lines[1] and "0.01" in lines[1]
    assert "SL" in lines[2] and "TP" in lines[2]
    assert "trend_breakout@1.0.0" in lines[3]


def test_the_closed_message_shows_the_result_and_the_total_balance_only() -> None:
    text = render_position_closed(closed())
    assert text.splitlines() == ["✅ BTCUSD : +10.00 $", "Solde : 1 010.00 $"]
    loss = render_position_closed(closed(pnl=Decimal("-10.00"), balance=Decimal("990.00")))
    assert loss.splitlines()[0] == "❌ BTCUSD : -10.00 $"
    flat = render_position_closed(closed(pnl=Decimal("0"), balance=Decimal("1000.00")))
    assert flat.splitlines()[0] == "⚪ BTCUSD : 0.00 $"


def test_the_account_currency_drives_the_symbol() -> None:
    assert money(Decimal("1234.5"), "EUR") == "1 234.50 €"
    assert money(Decimal("1234.5"), "usd") == "1 234.50 $"
    assert signed_money(Decimal("-3"), "EUR") == "-3.00 €"
    assert signed_money(Decimal("3"), "EUR") == "+3.00 €"


def test_no_fraction_of_the_volume_is_dropped() -> None:
    text = render_position_opened(opened(volume=Decimal("0.10")))
    assert "0.1 lot" in text  # one significant display, not a truncated zero
