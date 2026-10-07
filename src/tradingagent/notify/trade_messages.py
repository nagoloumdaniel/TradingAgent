"""Compact trade notifications for the operator (M-02, cahier v3 §37).

The operator asked for short messages: what was opened, and on close the result in
currency plus the account balance, nothing else. The detailed signal message (F-013) is a
different event and keeps its own mandatory fields.

Pure rendering: no clock, no network. Every data-derived string is HTML-escaped so the
message can be sent with Telegram's HTML parse mode. The figures sit in a `<pre>` block:
aligned columns, one line per figure, nothing to decode.
"""

import html
from dataclasses import dataclass
from decimal import Decimal

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.notify.commands import MODE_SHORT_LABELS

CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF", "JPY": "¥"}
DIRECTION_LABELS = {Direction.BUY: "ACHAT", Direction.SELL: "VENTE"}

# The column every value starts at inside a <pre> block of these messages.
LABEL_WIDTH = 12


@dataclass(frozen=True)
class PositionOpened:
    symbol: str
    direction: Direction
    volume: Decimal
    entry_price: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    strategy_ref: str
    mode: TradingMode
    ticket: int | None = None


@dataclass(frozen=True)
class PositionClosed:
    symbol: str
    pnl: Decimal
    balance: Decimal
    currency: str = "EUR"
    exit_reason: str = ""
    ticket: int | None = None


def money(value: Decimal, currency: str) -> str:
    """Thousands separated by a space, two decimals, and the account's currency symbol."""
    symbol = CURRENCY_SYMBOLS.get(currency.upper(), currency.upper())
    return f"{value:,.2f}".replace(",", " ") + f" {symbol}"


def signed_money(value: Decimal, currency: str) -> str:
    """`+10.00 $` for a gain, `-10.00 $` for a loss, `0.00 $` when flat."""
    prefix = "+" if value > 0 else ""
    return f"{prefix}{money(value, currency)}"


def _safe(text: str) -> str:
    return html.escape(text, quote=False)


def _row(label: str, value: str) -> str:
    return f"{label:<{LABEL_WIDTH}}{value}"


def _block(rows: list[str]) -> str:
    return "<pre>" + "\n".join(rows) + "</pre>"


def render_position_opened(notice: PositionOpened) -> str:
    identity = f"📈 OUVERT · {_safe(notice.symbol)} · {DIRECTION_LABELS[notice.direction]}"
    rows = [
        _row("Volume", f"{_volume(notice.volume)} lot"),
        _row("Entrée", _price(notice.entry_price)),
    ]
    # A missing level is left out rather than shown as a dash: the block stays aligned and
    # the operator is not invited to read a level that does not exist.
    if notice.stop_loss is not None:
        rows.append(_row("Stop", _price(notice.stop_loss)))
    if notice.take_profit is not None:
        rows.append(_row("Cible", _price(notice.take_profit)))
    footer = f"{_safe(notice.strategy_ref)} · {MODE_SHORT_LABELS[notice.mode]}"
    return "\n\n".join([identity, _block(rows), footer])


def render_position_closed(notice: PositionClosed) -> str:
    if notice.pnl > 0:
        icon = "✅"
    elif notice.pnl < 0:
        icon = "❌"
    else:
        icon = "⚪"
    rows = [
        _row("Résultat", signed_money(notice.pnl, notice.currency)),
        _row("Solde", money(notice.balance, notice.currency)),
    ]
    return "\n\n".join([f"{icon} {_safe(notice.symbol)}", _block(rows)])


def _price(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ")


def _volume(value: Decimal) -> str:
    return f"{value.normalize():f}"
