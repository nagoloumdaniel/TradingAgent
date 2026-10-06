"""The signal message sent to the operator (F-013, EF-005, TASK-021).

Pure rendering: no clock, no network. Every data-derived string is HTML-escaped, so the
message is sent with Telegram's HTML parse mode and no field can break the formatting.
"""

import html
from dataclasses import dataclass
from datetime import UTC, datetime

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.notify.commands import MODE_LABELS

DIRECTION_LABELS = {Direction.BUY: "ACHAT", Direction.SELL: "VENTE"}


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


def render_signal_message(notice: SignalNotice) -> str:
    def safe(text: str) -> str:
        return html.escape(text, quote=False)

    lines = [
        f"📈 SIGNAL — {safe(notice.symbol)} ({notice.timeframe.value})",
        f"Sens : {DIRECTION_LABELS[notice.direction]}",
        f"Mode : {MODE_LABELS[notice.mode]}",
        "",
        f"Prix observé : {_price(notice.observed_price)}",
        f"Zone d'entrée : {_price(notice.entry_low)} à {_price(notice.entry_high)}",
        f"Stop-loss : {_price(notice.stop_loss)}",
        f"Objectifs : {', '.join(_price(target) for target in notice.take_profits)}",
        f"Ratio risque/rendement estimé : {_risk_reward(notice)}",
        "",
        f"Stratégie : {safe(notice.strategy_ref)}",
        f"Justification : {safe(notice.reason)}",
        f"État du marché : {safe(notice.market_state)}",
    ]
    if notice.confidence is not None:
        lines.append(f"Confiance : {notice.confidence * 100:.0f} %")
    lines += [
        "",
        f"Identifiant : {safe(notice.ref)}",
        f"Généré le : {_utc(notice.generated_at)}",
        f"Expire le : {_utc(notice.expires_at)}",
    ]
    return "\n".join(lines)
