"""Cross-cutting invariants of every message the operator receives.

Each renderer is tested for its content next to its module; this file holds the rules that
apply to all of them at once — Telegram's HTML is well formed, data can never inject a tag,
the layout stays readable, and no message smuggles a URL or a file path onto the phone.
"""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.notify.signal_template import SignalNotice, render_signal_message
from tradingagent.notify.trade_messages import (
    PositionClosed,
    PositionOpened,
    render_position_closed,
    render_position_opened,
)

TAG = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>")
ALLOWED_TAGS = {"b", "i", "code", "pre"}
EMOJI = re.compile("[\U0001f300-\U0001faff\u2190-\u21ff\u2600-\u27bf\u2b00-\u2bff]")
URL = re.compile(r"https?://")
WINDOWS_PATH = re.compile(r"[A-Za-z]:\\")

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def assert_telegram_html(message: str) -> None:
    """Only Telegram's tags, each one closed, and nothing unbalanced."""
    stack: list[str] = []
    for match in TAG.finditer(message):
        raw, name = match.group(0), match.group(1)
        assert name in ALLOWED_TAGS, f"Telegram HTML does not know this tag: {raw}"
        if raw.startswith("</"):
            assert stack and stack.pop() == name, f"unbalanced tag: {raw}"
        else:
            stack.append(name)
    assert stack == [], f"unclosed tags: {stack}"


def signal(**overrides: object) -> str:
    values: dict[str, object] = {
        "ref": "witness@1.0.0:XAUUSD:M15:2026-10-06T12:00Z",
        "symbol": "XAUUSD",
        "direction": Direction.BUY,
        "observed_price": 2650.10,
        "entry_low": 2649.50,
        "entry_high": 2650.50,
        "stop_loss": 2647.50,
        "take_profits": (2652.50, 2655.50),
        "timeframe": Timeframe.M15,
        "strategy_ref": "witness@1.0.0",
        "generated_at": NOW,
        "expires_at": NOW + timedelta(minutes=45),
        "mode": TradingMode.DEMO,
        "market_state": "HEALTHY",
        "reason": "MM20 crossed above MM50",
    }
    values.update(overrides)
    return render_signal_message(SignalNotice(**values))  # type: ignore[arg-type]


def an_opened(**overrides: object) -> str:
    values: dict[str, object] = {
        "symbol": "BTCUSD",
        "direction": Direction.BUY,
        "volume": Decimal("0.01"),
        "entry_price": Decimal("84010.5"),
        "stop_loss": Decimal("83900"),
        "take_profit": Decimal("84500"),
        "strategy_ref": "trend_breakout@1.0.0",
        "mode": TradingMode.DEMO,
        "ticket": 555001,
    }
    values.update(overrides)
    return render_position_opened(PositionOpened(**values))  # type: ignore[arg-type]


def a_closed(**overrides: object) -> str:
    values: dict[str, object] = {
        "symbol": "BTCUSD",
        "pnl": Decimal("10.00"),
        "balance": Decimal("1010.00"),
        "currency": "USD",
        "exit_reason": "take_profit",
        "ticket": 555001,
    }
    values.update(overrides)
    return render_position_closed(PositionClosed(**values))  # type: ignore[arg-type]


EVERY_MESSAGE = {
    "signal": lambda: signal(),
    "signal with confidence": lambda: signal(confidence=0.72),
    "position opened": an_opened,
    "position closed": a_closed,
}


@pytest.mark.parametrize("name", sorted(EVERY_MESSAGE))
def test_the_html_is_well_formed_and_uses_only_telegram_tags(name: str) -> None:
    assert_telegram_html(EVERY_MESSAGE[name]())


@pytest.mark.parametrize("name", sorted(EVERY_MESSAGE))
def test_at_most_one_emoji_and_only_at_the_head(name: str) -> None:
    message = EVERY_MESSAGE[name]()
    found = EMOJI.findall(message)
    assert len(found) <= 1, message
    if found:
        assert EMOJI.match(message) is not None, "an emoji may only open the message"


@pytest.mark.parametrize("name", sorted(EVERY_MESSAGE))
def test_no_url_and_no_file_path_reaches_the_operator(name: str) -> None:
    message = EVERY_MESSAGE[name]()
    assert URL.search(message) is None
    assert WINDOWS_PATH.search(message) is None


@pytest.mark.parametrize("name", sorted(EVERY_MESSAGE))
def test_the_message_fits_a_phone_and_never_hits_the_telegram_limit(name: str) -> None:
    message = EVERY_MESSAGE[name]()
    assert len(message) < 4096
    assert max(len(line) for line in message.splitlines()) <= 80


def test_a_hostile_symbol_cannot_inject_a_tag() -> None:
    message = signal(symbol="<b>injecté</b> & <script>")
    assert "&lt;b&gt;injecté&lt;/b&gt; &amp; &lt;script&gt;" in message
    assert "<script>" not in message
    assert_telegram_html(message)


def test_a_hostile_justification_cannot_inject_a_tag() -> None:
    message = signal(reason="<i>x</i><pre>/etc/passwd</pre> &")
    assert "&lt;i&gt;x&lt;/i&gt;&lt;pre&gt;/etc/passwd&lt;/pre&gt; &amp;" in message
    assert message.count("<pre>") == message.count("</pre>") == 1
    assert_telegram_html(message)


def test_hostile_data_in_every_text_field_stays_escaped() -> None:
    message = signal(
        ref="a&b<c>",
        strategy_ref="<i>s</i>",
        market_state="<pre>etat</pre>",
        reason="& <script>alert(1)</script>",
    )
    for raw in ("<i>s</i>", "<pre>etat</pre>", "<script>", "a&b<c>"):
        assert raw not in message
    assert "a&amp;b&lt;c&gt;" in message
    assert_telegram_html(message)


def test_a_hostile_position_symbol_stays_escaped() -> None:
    opened = an_opened(symbol="<b>BTCUSD</b>", strategy_ref="<i>strat</i>")
    assert "<b>BTCUSD</b>" not in opened
    assert "&lt;b&gt;BTCUSD&lt;/b&gt;" in opened
    assert "<i>strat</i>" not in opened
    assert_telegram_html(opened)


def test_a_hostile_closed_symbol_stays_escaped() -> None:
    closed = a_closed(symbol="<b>BTCUSD</b>", pnl=Decimal("-5.00"))
    assert "<b>BTCUSD</b>" not in closed
    assert "&lt;b&gt;BTCUSD&lt;/b&gt;" in closed
    assert_telegram_html(closed)
