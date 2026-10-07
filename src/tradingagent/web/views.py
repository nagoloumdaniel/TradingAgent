"""Display rows shared by the rendered pages and the live SSE feed.

One builder per stream, used by both transports, so a row the operator sees on page load and
the same row pushed a second later can never disagree. The builders only turn values that
were already read or already computed into strings (§34): no figure is derived here.
"""

import json
from typing import Any

from tradingagent.core.states import SignalState
from tradingagent.web import format as display
from tradingagent.web.queries import OpenPositionView, SystemEventView

POSITION_COLUMNS: tuple[str, ...] = (
    "Marché",
    "Sens",
    "Volume",
    "Prix d'entrée",
    "Notionnel",
    "Mode",
    "Ouverte le (UTC)",
    "Âge",
)
ALERT_COLUMNS: tuple[str, ...] = ("Horodatage (UTC)", "Gravité", "Type", "Détail")


def position_cells(position: OpenPositionView) -> list[str]:
    return [
        position.symbol,
        display.direction_label(position.direction),
        display.number(position.volume, 2),
        display.number(position.open_price, 5),
        display.money(position.notional),
        display.mode_label(position.mode),
        display.moment(position.opened_at),
        display.duration(position.age),
    ]


def alert_cells(event: SystemEventView) -> list[str]:
    return [
        display.precise(event.occurred_at),
        display.severity_label(event.severity),
        event.kind,
        describe(event.detail),
    ]


def describe(detail: dict[str, Any]) -> str:
    """A stored JSON payload, rendered as one stable line — never re-serialised from a model."""
    if not detail:
        return ""
    return json.dumps(detail, ensure_ascii=False, sort_keys=True, default=str)


def state_label(state: str) -> str:
    """Signal states arrive as their stored value; the label table lives in ``format``."""
    try:
        return display.signal_state_label(SignalState(state))
    except ValueError:
        return state


__all__ = [
    "ALERT_COLUMNS",
    "POSITION_COLUMNS",
    "alert_cells",
    "describe",
    "position_cells",
    "state_label",
]
