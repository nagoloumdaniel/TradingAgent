"""What the operator sees in the terminal: signals and errors, and nothing else.

The engine logs JSON on purpose — a scheduled task, a journal and a monitoring agent all
read it — but a human watching a terminal does not want a stream of records, URLs and
heartbeats. This module is the other half: the same events, rendered for a person.

Two rules, and both are deliberate:

* **Nothing is re-worded.** The messages are already written for the operator (the signal
  template, the alert texts). Reformatting a message that reads well into a second wording
  would mean maintaining two vocabularies and having them drift. This unstrips the Telegram
  HTML, frames the text, colours it and adds a timestamp.
* **INFO never reaches the console.** A monitoring agent that prints every cycle is a
  monitoring agent nobody reads. Warnings and errors only, unless `--verbose` asks for more.

Colour is decided once, from the message itself — a signal, an alert, a stop — never from
the caller, so a new call site cannot forget to pass it.
"""

import html
import logging
import os
import re
import shutil
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TextIO

# --- ANSI, enabled properly on Windows ------------------------------------------------

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"
YELLOW = "\x1b[33m"
CYAN = "\x1b[36m"
GREY = "\x1b[90m"

_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)


def enable_ansi(stream: TextIO | None = None) -> bool:
    """Turn on virtual-terminal processing, so colour works in a Windows console.

    Python does not enable it for us: without this the escape codes are printed literally
    and the output looks worse than no colour at all. Best effort — a redirected stream, a
    non-Windows host or an old console all fail here, and none of them is an error.
    """
    if os.name != "nt":  # pragma: no cover - the Windows branch is the one under test
        return True
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetStdHandle(-11 if stream is None else -12)
        mode = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        enable_virtual_terminal_processing = 0x0004
        return bool(
            ctypes.windll.kernel32.SetConsoleMode(
                handle, mode.value | enable_virtual_terminal_processing
            )
        )
    except Exception:  # pragma: no cover - never worth failing a run over colour
        return False


def use_utf8_console() -> None:
    """Ask for UTF-8 so accented text and the frame characters survive.

    A Windows console defaults to a legacy code page; the box-drawing characters would come
    out as mojibake and French accents as question marks.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def supports_colour(stream: TextIO) -> bool:
    """Colour only when a person is watching: a redirected log must stay plain text."""
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


# --- rendering ------------------------------------------------------------------------


def plain_text(message: str) -> str:
    """Telegram speaks HTML; a terminal does not. Entities are decoded, tags removed."""
    return html.unescape(_TAG.sub("", _BREAK.sub("\n", message)))


def _width(stream: TextIO) -> int:
    columns = shutil.get_terminal_size((88, 24)).columns
    return max(48, min(columns - 2, 84))


def _kind(message: str) -> tuple[str, str]:
    """The colour and the label, read from the message itself.

    The order matters: an alert about a signal is still an alert, and a refusal is not a
    signal just because it mentions one.
    """
    upper = message.upper()
    if "SIGNAL" in upper:
        if "VENTE" in upper or "SELL" in upper:
            return RED, "SIGNAL"
        return GREEN, "SIGNAL"
    if "HALT" in upper or "ARRET" in upper or "ARRÊT" in upper or "CRITICAL" in upper:
        return RED, "ARRÊT"
    if "DIVERGENCE" in upper:
        return RED, "DIVERGENCE"
    if "COUPURE" in upper or "CUT" in upper:
        return YELLOW, "ALERTE"
    return YELLOW, "ALERTE"


def format_notice(message: str, *, now: datetime, colour: bool = True) -> str:
    """Frame one operator message. Pure but for the injected clock."""
    text = plain_text(message).strip()
    if not text:
        return ""
    lines = text.splitlines()
    accent, label = _kind(text)
    width = 0  # set below; the frame adapts to the widest line
    body = [line.rstrip() for line in lines]
    width = _width(sys.stdout) - 4
    stamp = now.astimezone(UTC).strftime("%H:%M:%S")

    def paint(code: str, value: str) -> str:
        return f"{code}{value}{RESET}" if colour else value

    head_left = f"{stamp}  {label}"
    head = paint(accent + BOLD, head_left)
    rule = paint(accent, "─" * width)
    rendered = [f"{paint(GREY, '┌')}{rule}", f"{paint(accent, '│')} {head}"]
    for line in body:
        if line:
            rendered.append(f"{paint(accent, '│')} {line}")
        else:
            rendered.append(f"{paint(accent, '│')}")
    rendered.append(f"{paint(GREY, '└')}{rule}")
    return "\n".join(rendered)


class ConsoleNotifier:
    """Prints every operator message, then hands it to the real notifier unchanged.

    The engine already sends its signals and alerts to Telegram; the console is a second
    reader of the same text, never a second source. A failure to print must not cost the
    message, so the inner notifier is always called.
    """

    def __init__(
        self,
        inner: Any,
        *,
        stream: TextIO | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._inner = inner
        self._stream = stream if stream is not None else sys.stdout
        self._now = now if now is not None else lambda: datetime.now(UTC)

    async def send(self, text: str, *, parse_mode: str | None = None) -> bool:
        try:
            rendered = format_notice(text, now=self._now(), colour=supports_colour(self._stream))
            if rendered:
                print(rendered, file=self._stream, flush=True)
        except Exception as error:  # a broken console must never swallow a signal
            print(f"console: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return await self._inner.send(text, parse_mode=parse_mode)


# --- logging --------------------------------------------------------------------------


class ConsoleFormatter(logging.Formatter):
    """One readable line for a warning or an error, with the logger's own context."""

    def __init__(self, *, colour: bool = True) -> None:
        super().__init__()
        self._colour = colour

    def format(self, record: logging.LogRecord) -> str:
        moment = datetime.fromtimestamp(record.created, tz=UTC).strftime("%H:%M:%S")
        level = record.levelname
        code = RED if record.levelno >= logging.ERROR else YELLOW
        message = record.getMessage()
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if self._colour:
            head = f"{GREY}{moment}{RESET} {code}{BOLD}{level:<8}{RESET}"
        else:
            head = f"{moment} {level:<8}"
        line = f"{head} {message}"
        if record.exc_text:
            line = f"{line}\n{record.exc_text}"
        return line


# Loggers whose INFO is noise on a console and whose purpose is machine diagnostics.
NOISY_LOGGERS = ("httpx", "httpcore", "telegram", "telegram.ext", "urllib3", "asyncio")


def configure_console_logging(level: int | str, *, stream: TextIO | None = None) -> None:
    """Replace the JSON handler with a readable one, and quiet the libraries.

    Warnings and errors only by default: an agent that narrates every cycle is an agent
    nobody watches. `--verbose` raises the level rather than adding a second handler, so
    there is exactly one place where the verbosity is decided.
    """
    target = logging.getLogger()
    target.setLevel(logging._nameToLevel[level] if isinstance(level, str) else level)
    for existing in list(target.handlers):
        target.removeHandler(existing)
    sink = stream if stream is not None else sys.stderr
    handler = logging.StreamHandler(sink)
    handler.setFormatter(ConsoleFormatter(colour=supports_colour(sink)))
    handler._tradingagent_console = True  # type: ignore[attr-defined]
    target.addHandler(handler)
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


__all__ = [
    "ConsoleFormatter",
    "ConsoleNotifier",
    "configure_console_logging",
    "enable_ansi",
    "format_notice",
    "plain_text",
    "supports_colour",
    "use_utf8_console",
]
