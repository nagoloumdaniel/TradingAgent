"""What a command answers with: text, the buttons a choice needs, and how to render it.

Replies stay Telegram-free: the router, the service and every handler speak about a
`Keyboard` of `Button`s, and `telegram_app` is the single place that knows how Telegram
spells one.

**Plain by default, rich on purpose.** A reply carries no parse mode unless it asks for
one, so a message built from data — a price, a symbol, an error from the terminal — can
never be mangled by a stray `<` or `&`. The screens the operator reads to *choose* (the
guide, one command's own page) opt into `HTML` and use Telegram's own vocabulary: bold
for the action, `<code>` for what to type, `<blockquote>` for the consequence. Those
screens are written here, never interpolated from market data.

`callback_data` is untrusted input. It *names* a command and its arguments; it never
carries authority. It is short (Telegram refuses more than 64 bytes) and it goes stale
after `CALLBACK_TTL`, and every click is replayed through the same audited path as the
typed command before anything happens.
"""

import string
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

MAX_CALLBACK_BYTES = 64
CALLBACK_PREFIX = "c1"
CALLBACK_TTL = timedelta(minutes=15)

#: What a reply asks the adapter for. Telegram spells it exactly this way.
HTML = "HTML"

_COMMAND_CHARS = frozenset(string.ascii_lowercase + "_")
_ARG_CHARS = frozenset(string.ascii_letters + string.digits + "_.@+-")
_MAX_PART = 32


@dataclass(frozen=True)
class Button:
    label: str
    data: str


@dataclass(frozen=True)
class Keyboard:
    """Rows of buttons; the outer tuple is the shape the operator sees."""

    rows: tuple[tuple[Button, ...], ...]


class Reply(str):
    """An answer, optionally carrying the buttons of the choice it offers and its markup.

    A `str` subclass on purpose: every existing caller keeps comparing and printing it
    as the text it is, and only the Telegram adapter looks at `keyboard` and `parse_mode`.
    """

    keyboard: Keyboard | None
    parse_mode: str | None

    def __new__(
        cls, text: str, keyboard: Keyboard | None = None, parse_mode: str | None = None
    ) -> "Reply":
        reply = super().__new__(cls, text)
        reply.keyboard = keyboard
        reply.parse_mode = parse_mode
        return reply


def as_reply(value: str) -> Reply:
    """A handler's answer, with buttons when it has some and as plain text otherwise."""
    return value if isinstance(value, Reply) else Reply(value)


def grid(buttons: Sequence[Button], per_row: int = 2) -> Keyboard:
    """Lay buttons out without leaving a row of one at the end."""
    count = len(buttons)
    if count == 0:
        return Keyboard(())
    width = max(1, min(per_row, count))
    rows = tuple(tuple(buttons[start : start + width]) for start in range(0, count, width))
    return Keyboard(rows)


@dataclass(frozen=True)
class Callback:
    command: str
    args: tuple[str, ...]
    issued_at: datetime


def encode_callback(command: str, args: Sequence[str], at: datetime) -> str:
    """The payload of one button. A bug here is a bug, not operator input: it raises."""
    data = ":".join((CALLBACK_PREFIX, str(int(at.timestamp())), command, *args))
    if not _is_addressable(command, tuple(args)):
        raise ValueError(f"a button may only name a command and plain arguments: {data!r}")
    if len(data.encode()) > MAX_CALLBACK_BYTES:
        raise ValueError(f"callback_data is longer than Telegram allows: {data!r}")
    return data


def decode_callback(data: str) -> Callback | None:
    """The callback, or None for anything that is not exactly one of our buttons.

    Strict on purpose: the payload arrives from the network and may have been written by
    hand. Anything doubtful — a foreign prefix, a command with capitals, an argument with
    punctuation we never emit, an over-long string — is not a button we made.
    """
    if not data or len(data.encode()) > MAX_CALLBACK_BYTES:
        return None
    parts = data.split(":")
    if len(parts) < 3 or parts[0] != CALLBACK_PREFIX:
        return None
    stamp, command, args = parts[1], parts[2], tuple(parts[3:])
    if not stamp.isdigit() or len(stamp) > 12:
        return None
    if not _is_addressable(command, args):
        return None
    return Callback(command, args, datetime.fromtimestamp(int(stamp), UTC))


def is_fresh(callback: Callback, at: datetime, ttl: timedelta = CALLBACK_TTL) -> bool:
    """True while the message the button was attached to is still the current one.

    A button stamped in the future is not trusted either: that is a clock we do not know.
    """
    return timedelta(0) <= at - callback.issued_at <= ttl


def _is_addressable(command: str, args: tuple[str, ...]) -> bool:
    if not command or len(command) > _MAX_PART or not set(command) <= _COMMAND_CHARS:
        return False
    return all(arg and len(arg) <= _MAX_PART and set(arg) <= _ARG_CHARS for arg in args)
