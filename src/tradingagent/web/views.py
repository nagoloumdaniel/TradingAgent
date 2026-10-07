"""Display rows shared by the rendered pages and the live SSE feed.

One builder per stream, used by both transports, so a row the operator sees on page load and
the same row pushed a second later can never disagree. The builders only turn values that
were already read or already computed into strings (§34): no figure is derived here — except
the trade timeline, which orders stored timestamps and invents nothing.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tradingagent.core.states import SignalState
from tradingagent.web import format as display
from tradingagent.web.queries import (
    OpenPositionView,
    PositionListView,
    SystemEventView,
    TradeReplay,
)

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
# The positions page shows the whole history, closed rows included — which is why it also
# carries the lifecycle columns (state, ticket, exit reason) the live feed does not need.
POSITION_TABLE_COLUMNS: tuple[str, ...] = (
    "Marché",
    "Sens",
    "État",
    "Volume",
    "Prix d'entrée",
    "Notionnel",
    "Mode",
    "Ticket",
    "Motif de sortie",
    "Ouverte le (UTC)",
    "Âge",
)
# Right-aligned, monospace columns: money, volume and the ticket. Everything else reads left.
POSITION_NUMERIC_COLUMNS: frozenset[str] = frozenset(
    {"Volume", "Prix d'entrée", "Notionnel", "Ticket"}
)
ALERT_COLUMNS: tuple[str, ...] = ("Horodatage (UTC)", "Gravité", "Type", "Détail")

# Which step comes first when two timestamps are identical (the same second is common: the
# risk verdict is written in the same cycle as the signal). Ordering, never invention.
TIMELINE_RANK: dict[str, int] = {
    "signal": 0,
    "risk": 1,
    "order": 2,
    "execution": 3,
    "position": 4,
    "event": 5,
    "close": 6,
    "analysis": 7,
}


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


def alert_classes(events: Sequence[SystemEventView]) -> list[str]:
    """The badge class per alert, so the browser colours a severity it never interprets."""
    return [display.severity_class(event.severity) for event in events]


def position_list_cells(position: PositionListView) -> list[str]:
    """One row of the positions page, in the order of :data:`POSITION_TABLE_COLUMNS`."""
    return [
        position.symbol,
        display.direction_label(position.direction),
        display.position_state_label(position.state),
        display.number(position.volume, 2),
        display.number(position.open_price, 5),
        display.money(position.notional),
        display.mode_label(position.mode),
        str(position.ticket),
        position.exit_reason or display.NA,
        display.moment(position.opened_at),
        display.duration(position.age),
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


# ---------------------------------------------------------------------------------------
# Trade replay (§28): the control-by-control verdict and the chronological spine.
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckRow:
    """One risk control as it was persisted: its name, its reading, and how to badge it."""

    name: str
    value: str
    state: str  # "ok" | "bad" | "" — empty means the control is not a boolean


@dataclass(frozen=True)
class TimelineEntry:
    """One step of the replay, already ordered and already worded."""

    at: datetime
    kind: str
    label: str
    detail: str


def check_rows(checks: Mapping[str, Any] | None) -> list[CheckRow]:
    """Every control of a stored ``checks`` payload, in a stable order.

    A boolean is read as a verdict; anything else (a limit, a count, a ratio) is shown as
    it was stored, without a badge, because the dashboard does not know the threshold.
    """
    if not checks:
        return []
    rows: list[CheckRow] = []
    for name in sorted(checks):
        value = checks[name]
        if isinstance(value, bool):
            rows.append(CheckRow(name, "vérifié" if value else "refusé", "ok" if value else "bad"))
        else:
            rows.append(CheckRow(name, _reading(value), ""))
    return rows


def _reading(value: Any) -> str:
    if isinstance(value, dict):
        return describe(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def replay_timeline(replay: TradeReplay) -> list[TimelineEntry]:
    """The stored timestamps of one trade, merged and ordered: the replay itself.

    Nothing is inferred here. Every entry carries a moment the database holds and a value it
    holds; the only decision this function makes is the order in which two simultaneous
    steps are read, and that is fixed by :data:`TIMELINE_RANK`.
    """
    signal = replay.signal
    entries: list[TimelineEntry] = [
        TimelineEntry(
            at=signal.generated_at,
            kind="signal",
            label="Signal généré",
            detail=(
                f"{signal.symbol} {display.direction_label(signal.direction)} "
                f"{signal.timeframe.value} — {signal.reason}"
            ),
        )
    ]

    if replay.risk is not None:
        entries.append(
            TimelineEntry(
                at=replay.risk.decided_at,
                kind="risk",
                label=f"Risque : {display.risk_outcome_label(replay.risk.outcome)}",
                detail=replay.risk.reason,
            )
        )

    order = replay.order
    if order is not None:
        entries.append(
            TimelineEntry(
                at=order.created_at,
                kind="order",
                label="Ordre créé",
                detail=(
                    f"{display.mode_label(order.mode)} · volume {display.number(order.volume, 2)} "
                    f"· prix demandé {display.number(order.requested_price, 5)}"
                ),
            )
        )
        if order.updated_at > order.created_at:
            entries.append(
                TimelineEntry(
                    at=order.updated_at,
                    kind="order",
                    label=f"Ordre mis à jour : {display.order_state_label(order.state)}",
                    detail=(
                        f"retcode {order.retcode if order.retcode is not None else display.NA}"
                        f" · {order.broker_comment or 'sans commentaire courtier'}"
                    ),
                )
            )

    for execution in replay.executions:
        entries.append(
            TimelineEntry(
                at=execution.executed_at,
                kind="execution",
                label=f"Exécution {execution.broker_deal_ticket}",
                detail=(
                    f"prix obtenu {display.number(execution.price, 5)} · "
                    f"volume {display.number(execution.volume, 2)} · "
                    f"glissement {display.number(execution.slippage, 4)}"
                ),
            )
        )

    for event in replay.events:
        entries.append(
            TimelineEntry(
                at=event.occurred_at,
                kind="event",
                label=display.execution_kind_label(event.kind),
                detail=_event_detail(event.detail, event.elapsed_ms),
            )
        )

    if replay.position is not None:
        entries.append(
            TimelineEntry(
                at=replay.position.opened_at,
                kind="position",
                label="Position ouverte",
                detail=(
                    f"ticket {replay.position.broker_position_ticket} · entrée "
                    f"{display.number(replay.position.open_price, 5)} · stop "
                    f"{display.number(replay.position.stop_loss, 5)} · cible "
                    f"{display.number(replay.position.take_profit, 5)}"
                ),
            )
        )

    if replay.trade is not None:
        entries.append(
            TimelineEntry(
                at=replay.trade.closed_at,
                kind="close",
                label="Position clôturée",
                detail=(
                    f"{replay.exit_reason or 'motif non enregistré'} · prix "
                    f"{display.number(replay.close_price, 5)} · "
                    f"{display.money(replay.trade.pnl_eur, signed=True)}"
                ),
            )
        )

    for analysis in replay.analyses:
        entries.append(
            TimelineEntry(
                at=analysis.created_at,
                kind="analysis",
                label=f"Analyse IA : {display.analysis_kind_label(analysis.kind)}",
                detail=analysis.response or describe(analysis.findings),
            )
        )

    return sorted(entries, key=lambda entry: (entry.at, TIMELINE_RANK.get(entry.kind, 99)))


def _event_detail(detail: Mapping[str, Any], elapsed_ms: int | None) -> str:
    payload = {key: value for key, value in detail.items() if key != "elapsed_ms"}
    described = describe(dict(payload))
    latency = display.milliseconds(elapsed_ms)
    return f"{described} · latence {latency}" if described else f"latence {latency}"


def replay_checks(replay: TradeReplay) -> Sequence[CheckRow]:
    return check_rows(None if replay.risk is None else replay.risk.checks)


__all__ = [
    "ALERT_COLUMNS",
    "POSITION_COLUMNS",
    "POSITION_NUMERIC_COLUMNS",
    "POSITION_TABLE_COLUMNS",
    "TIMELINE_RANK",
    "CheckRow",
    "TimelineEntry",
    "alert_cells",
    "alert_classes",
    "check_rows",
    "describe",
    "position_cells",
    "position_list_cells",
    "replay_checks",
    "replay_timeline",
    "state_label",
]
