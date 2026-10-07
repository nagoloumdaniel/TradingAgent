"""The signal message sent to the operator (F-013, EF-005, TASK-021).

Pure rendering: no clock, no network. Every data-derived string is HTML-escaped, so the
message is sent with Telegram's HTML parse mode and no field can break the formatting.

The shape is deliberate. The operator reads this on a phone, so the message is four blocks
separated by a blank line — identity, numbers, justification, identifiers. The numbers sit
in a `<pre>` block: monospace is the only way to get a real column in Telegram, and two
signals can then be compared at a glance. Values start at `LABEL_WIDTH`, the labels are
short, and nothing is padded with filler beyond that one column.
"""

import html
from dataclasses import dataclass
from datetime import UTC, datetime

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.notify.commands import MODE_SHORT_LABELS

DIRECTION_LABELS = {Direction.BUY: "ACHAT", Direction.SELL: "VENTE"}

# One emoji at most, at the head, and only when it says something the text does not.
DIRECTION_ICONS = {Direction.BUY: "📈", Direction.SELL: "📉"}

# The column every value starts at inside the <pre> block: the widest label, plus two.
LABEL_WIDTH = 18


@dataclass(frozen=True)
class SignalNotice:
    """One validated signal, with everything F-013 requires the operator to see."""

    ref: str  # idempotency key, the signal's unique identifier
    symbol: str
    direction: Direction
    observed_price: float
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profits: tuple[float, ...]
    timeframe: Timeframe
    strategy_ref: str  # e.g. witness@1.0.0
    generated_at: datetime
    expires_at: datetime
    mode: TradingMode
    market_state: str  # series health as assessed when the signal was generated
    reason: str
    confidence: float | None = None  # strategies that produce one; omitted otherwise


def _price(value: float) -> str:
    return f"{value:.2f}"


def _utc(moment: datetime) -> str:
    return f"{moment.astimezone(UTC):%Y-%m-%d %H:%M} UTC"


def _risk_reward(notice: SignalNotice) -> str:
    """Estimated from the middle of the entry zone to the first target."""
    entry_mid = (notice.entry_low + notice.entry_high) / 2
    risk = abs(entry_mid - notice.stop_loss)
    reward = abs(notice.take_profits[0] - entry_mid)
    if risk == 0:
        return "indéterminable"
    return f"{reward / risk:.1f}"


def _safe(text: str) -> str:
    return html.escape(text, quote=False)


def _row(label: str, value: str) -> str:
    return f"{label:<{LABEL_WIDTH}}{value}"


def _mode_line(mode: TradingMode) -> str:
    """Real money is the one mode that gets emphasis; the others are simply named."""
    label = MODE_SHORT_LABELS[mode]
    if mode is TradingMode.LIVE:
        return f"<b>Mode : {label}</b>"
    return f"Mode : {label}"


def render_signal_message(notice: SignalNotice) -> str:
    identity = (
        f"{DIRECTION_ICONS[notice.direction]} SIGNAL · {_safe(notice.symbol)} · "
        f"{_safe(notice.timeframe.value)} · {DIRECTION_LABELS[notice.direction]}"
    )

    numbers = [
        _row("Prix observé", _price(notice.observed_price)),
        _row("Entrée", f"{_price(notice.entry_low)} - {_price(notice.entry_high)}"),
        _row("Stop-loss", _price(notice.stop_loss)),
        _row("Objectifs", " · ".join(_price(target) for target in notice.take_profits)),
        _row("Risque/rendement", _risk_reward(notice)),
    ]

    justification = [
        _mode_line(notice.mode),
        f"Stratégie : {_safe(notice.strategy_ref)}",
        f"Motif : {_safe(notice.reason)}",
        f"Marché : {_safe(notice.market_state)}",
    ]
    if notice.confidence is not None:
        justification.append(f"Confiance : {notice.confidence * 100:.0f} %")

    identifiers = [
        f"Généré : {_utc(notice.generated_at)}",
        f"Expire le : {_utc(notice.expires_at)}",
        f"Réf : {_safe(notice.ref)}",
    ]

    return "\n\n".join(
        [
            identity,
            "<pre>" + "\n".join(numbers) + "</pre>",
            "\n".join(justification),
            "\n".join(identifiers),
        ]
    )
