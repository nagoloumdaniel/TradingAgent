"""The operator's compact trade messages (cahier v3, §37)."""

import dataclasses
import re
from decimal import Decimal

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.notify.trade_messages import (
    LABEL_WIDTH,
    PositionClosed,
    PositionOpened,
    money,
    render_position_closed,
    render_position_opened,
    signed_money,
)

PRE = re.compile(r"<pre>(.*?)</pre>", re.DOTALL)

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


def aligned_columns(block: str) -> set[int]:
    columns = set()
    for line in block.splitlines():
        match = re.match(r"^(\S.*?)\s{2,}(\S.*)$", line)
        assert match is not None, f"line is not label + value: {line!r}"
        columns.add(match.start(2))
    return columns


def test_the_opened_message_is_short_and_carries_the_position_essentials() -> None:
    text = render_position_opened(opened())
    lines = text.splitlines()
    assert lines[0] == "📈 OUVERT · BTCUSD · ACHAT"
    assert lines[1] == ""
    assert lines[2].startswith("<pre>")
    assert "0.01 lot" in text
    assert "84 010.50" in text  # the executed entry
    assert "83 900.00" in text  # the stop
    assert "84 500.00" in text  # the target
    assert lines[-1] == "trend_breakout@1.0.0 · DÉMO"
    assert len(text.splitlines()) <= 8


def test_the_opened_figures_are_aligned() -> None:
    [block] = PRE.findall(render_position_opened(opened()))
    assert aligned_columns(block) == {LABEL_WIDTH}


def test_a_missing_level_is_left_out_rather_than_drawn_as_a_dash() -> None:
    text = render_position_opened(opened(stop_loss=None))
    assert "Stop" not in text
    assert "Cible" in text
    [block] = PRE.findall(text)
    assert aligned_columns(block) == {LABEL_WIDTH}


def test_the_closed_message_shows_the_result_and_the_total_balance_only() -> None:
    lines = render_position_closed(closed()).splitlines()
    assert lines[0] == "✅ BTCUSD"
    assert lines[1] == ""
    assert lines[2].startswith("<pre>")
    assert lines[-1].endswith("</pre>")
    [block] = PRE.findall(render_position_closed(closed()))
    assert "Résultat" in block and "+10.00 $" in block
    assert "Solde" in block and "1 010.00 $" in block
    assert aligned_columns(block) == {LABEL_WIDTH}


def test_the_closed_icon_follows_the_sign_of_the_result() -> None:
    loss = render_position_closed(closed(pnl=Decimal("-10.00"), balance=Decimal("990.00")))
    assert loss.splitlines()[0] == "❌ BTCUSD"
    flat = render_position_closed(closed(pnl=Decimal("0"), balance=Decimal("1000.00")))
    assert flat.splitlines()[0] == "⚪ BTCUSD"


def test_the_account_currency_drives_the_symbol() -> None:
    assert money(Decimal("1234.5"), "EUR") == "1 234.50 €"
    assert money(Decimal("1234.5"), "usd") == "1 234.50 $"
    assert signed_money(Decimal("-3"), "EUR") == "-3.00 €"
    assert signed_money(Decimal("3"), "EUR") == "+3.00 €"


def test_no_fraction_of_the_volume_is_dropped() -> None:
    text = render_position_opened(opened(volume=Decimal("0.10")))
    assert "0.1 lot" in text  # one significant display, not a truncated zero
