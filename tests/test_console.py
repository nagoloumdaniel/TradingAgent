"""What the operator sees in the terminal.

The console is a second *reader* of messages the engine already sends to Telegram, so the
tests care about two things: the message survives the trip unchanged, and the framing is
readable without colour (a redirected terminal, `NO_COLOR`, a log file).
"""

import asyncio
import io
import logging
from datetime import UTC, datetime

import pytest

from tradingagent.console import (
    ConsoleFormatter,
    ConsoleNotifier,
    format_notice,
    plain_text,
)
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.notify.signal_template import SignalNotice, render_signal_message

NOW = datetime(2026, 10, 7, 22, 30, 15, tzinfo=UTC)


def a_signal(direction: Direction = Direction.BUY) -> str:
    return render_signal_message(
        SignalNotice(
            ref="witness:1.1.0:XAUUSD:M15:abc",
            symbol="XAUUSD",
            direction=direction,
            observed_price=2400.50,
            entry_low=2400.0,
            entry_high=2401.0,
            stop_loss=2390.0,
            take_profits=(2410.0, 2420.0),
            timeframe=Timeframe.M15,
            strategy_ref="witness@1.1.0",
            generated_at=NOW,
            expires_at=NOW,
            mode=TradingMode.SIGNAL,
            market_state="série saine",
            reason="croisement haussier",
        )
    )


# --- the message survives the trip ------------------------------------------------------


def test_html_tags_are_removed_not_shown() -> None:
    """Telegram escapes the operator's text; a terminal must not print the escapes."""
    assert plain_text("<b>gras</b> et &amp; esperluette") == "gras et & esperluette"


def test_a_line_break_tag_becomes_a_real_line_break() -> None:
    assert plain_text("un<br/>deux") == "un\ndeux"


def test_the_transmitted_message_keeps_its_original_markup() -> None:
    """The notifier prints a rendered copy; Telegram still receives the HTML."""

    class Recording:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, text: str, *, parse_mode: str | None = None) -> bool:
            self.sent.append(text)
            return True

    inner = Recording()
    stream = io.StringIO()
    notice = a_signal()
    notifier = ConsoleNotifier(inner, stream=stream, now=lambda: NOW)

    assert asyncio.run(notifier.send(notice, parse_mode="HTML")) is True
    assert inner.sent == [notice], "the real notifier must receive the message unchanged"
    assert "<b>" not in stream.getvalue(), "the console prints text, not markup"


# --- the framing --------------------------------------------------------------------------


def test_a_signal_is_framed_and_labelled() -> None:
    rendered = format_notice(a_signal(), now=NOW, colour=False)
    lines = rendered.splitlines()
    assert lines[0].startswith("┌")
    assert lines[-1].startswith("└")
    assert "22:30:15" in lines[1]
    assert "SIGNAL" in lines[1]
    assert any("ACHAT" in line for line in lines)
    assert any("XAUUSD" in line for line in lines)


def test_the_frame_never_contains_escape_codes_when_colour_is_off() -> None:
    """A redirected terminal, a log file or NO_COLOR must stay plain text."""
    rendered = format_notice(a_signal(), now=NOW, colour=False)
    assert "\x1b" not in rendered


@pytest.mark.parametrize(
    ("direction", "expected"),
    [(Direction.BUY, "SIGNAL"), (Direction.SELL, "SIGNAL")],
)
def test_a_sell_is_labelled_like_a_buy(direction: Direction, expected: str) -> None:
    """Only the colour differs; the label is what a colour-blind reader depends on."""
    rendered = format_notice(a_signal(direction), now=NOW, colour=False)
    assert expected in rendered.splitlines()[1]


def test_an_alert_is_not_labelled_a_signal() -> None:
    rendered = format_notice("Coupure du terminal détectée", now=NOW, colour=False)
    assert "ALERTE" in rendered.splitlines()[1]


def test_a_stop_is_its_own_label() -> None:
    rendered = format_notice("ARRET GLOBAL : drawdown quotidien atteint", now=NOW, colour=False)
    assert "ARRÊT" in rendered.splitlines()[1]


def test_an_empty_message_produces_nothing() -> None:
    assert format_notice("   \n  ", now=NOW, colour=False) == ""


# --- the console never costs the operator a message -----------------------------------------


def test_a_broken_console_still_delivers_to_telegram() -> None:
    """A printing failure is a display problem, never a reason to drop a signal."""

    class Exploding(io.StringIO):
        def write(self, *args: object, **kwargs: object) -> int:
            raise OSError("console closed")

    class Recording:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, text: str, *, parse_mode: str | None = None) -> bool:
            self.sent.append(text)
            return True

    inner = Recording()
    notifier = ConsoleNotifier(inner, stream=Exploding(), now=lambda: NOW)

    assert asyncio.run(notifier.send("hello")) is True
    assert inner.sent == ["hello"]


# --- the log line ---------------------------------------------------------------------------


def test_a_warning_is_one_readable_line() -> None:
    record = logging.LogRecord("x", logging.WARNING, __file__, 1, "attention %s", ("ici",), None)
    line = ConsoleFormatter(colour=False).format(record)
    assert "WARNING" in line
    assert "attention ici" in line
    assert "{" not in line, "a person is reading this, not a log shipper"


def test_an_exception_is_reported_after_the_line() -> None:
    try:
        raise ValueError("panne")
    except ValueError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "echec", (), sys.exc_info())
    line = ConsoleFormatter(colour=False).format(record)
    assert "echec" in line
    assert "ValueError: panne" in line
