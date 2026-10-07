"""File protocol between the agent and the MQL5 Guardian EAs (F-016, F-017, RM-013, RM-014).

The backend is the only brain. The EA is a hand: it executes the orders the backend
authorises, it protects the stops, it watches the expected state against the account, and
it reports what it did. The two sides never share memory, only two directories of JSON
files:

``<directory>/state/<SYMBOL>_state.json``
    Written by Python with :func:`publish_state`: the expected positions, the stops, the
    protective limits, the kill switch and the heartbeat, all timestamped in UTC. The EA
    only ever acts on an order found in this file â€” never on its own initiative.

``<directory>/reports/<SYMBOL>_report.json``
    Written by the EA: heartbeat, connection state, applied revision, observed positions
    and the journal of what happened (order, execution, price, volume, stop, target,
    result, slippage, errors, connection losses).

``<directory>/reports/<SYMBOL>_events.jsonl``
    The same events, appended one JSON object per line, so nothing is lost when a report
    is overwritten.

Every write goes through a temporary file in the same directory followed by
:func:`os.replace`, so a reader never sees a half-written document. Every read tolerates
a missing or corrupt file: a file that cannot be parsed is skipped, never raised, because
an unreadable report is an *offline* EA, not a crash of the agent.

Nothing here imports the terminal or the executor: the bridge is testable without MT5 and
stays on the safe side of the architecture gate (`tests/test_architecture.py`).
"""

import contextlib
import json
import os
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from tradingagent.core.market import Direction

#: Version of the file contract. Bumped whenever a field changes meaning, never when one
#: is merely added; the EA refuses a state file whose version it does not know.
PROTOCOL_VERSION = 1

#: A heartbeat older than this makes the EA OFFLINE. The EA writes every two seconds, so
#: the tolerance absorbs a handful of missed writes before the dashboard cries wolf.
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 15.0

STATE_DIR_NAME = "state"
REPORTS_DIR_NAME = "reports"
STATE_SUFFIX = "_state.json"
REPORT_SUFFIX = "_report.json"
EVENTS_SUFFIX = "_events.jsonl"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class EaStatus(StrEnum):
    """What the dashboard shows for one EA. Anything unreadable is OFFLINE."""

    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"


class OrderAction(StrEnum):
    """The only two things the backend may ask an EA to do."""

    OPEN = "OPEN"
    CLOSE = "CLOSE"


@dataclass(frozen=True)
class ExpectedPosition:
    """A position the backend believes is open, with the stop it must carry."""

    ticket: int
    direction: Direction
    volume: float
    stop_loss: float | None = None
    take_profit: float | None = None
    comment: str = ""

    def as_json(self) -> dict[str, Any]:
        return {
            "ticket": self.ticket,
            "direction": self.direction.value.upper(),
            "volume": self.volume,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "comment": self.comment,
        }


@dataclass(frozen=True)
class AuthorisedOrder:
    """One order the backend authorises. Its `order_id` is carried in the MT5 comment.

    The EA deduplicates on `comment`: an order whose comment is already on a position â€” or
    already recorded as applied â€” is never sent a second time (RM-012, F-012).
    """

    order_id: str
    action: OrderAction
    direction: Direction
    volume: float
    stop_loss: float | None = None
    take_profit: float | None = None
    comment: str = ""
    position_ticket: int | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.order_id,
            "action": self.action.value,
            "direction": self.direction.value.upper(),
            "volume": self.volume,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "comment": self.comment,
            "position_ticket": self.position_ticket,
        }


@dataclass(frozen=True)
class EaState:
    """The whole document Python publishes for one EA."""

    symbol: str
    magic: int
    revision: int
    published_at: datetime
    kill_switch: bool = False
    max_positions: int = 1
    max_total_volume: float | None = None
    positions: tuple[ExpectedPosition, ...] = ()
    orders: tuple[AuthorisedOrder, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "symbol": self.symbol,
            "magic": self.magic,
            "revision": self.revision,
            "published_at": format_utc(self.published_at),
            # The epoch is what MQL5 can read without a date parser; the ISO form is what
            # a human and the Python side read. Both describe the same instant.
            "published_epoch": int(self.published_at.timestamp()),
            "heartbeat_timeout_seconds": DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
            "kill_switch": self.kill_switch,
            "limits": {
                "max_positions": self.max_positions,
                "max_total_volume": self.max_total_volume,
            },
            "positions": [position.as_json() for position in self.positions],
            "orders": [order.as_json() for order in self.orders],
        }


@dataclass(frozen=True)
class ReportedPosition:
    """A position as the EA sees it in the terminal."""

    ticket: int
    symbol: str
    direction: Direction | None
    volume: float
    price_open: float
    stop_loss: float
    take_profit: float
    profit: float
    comment: str


@dataclass(frozen=True)
class EaEvent:
    """One line of the EA's journal. `kind` is a stable label, `message` is for the operator."""

    seq: int
    at: datetime
    kind: str
    severity: str
    message: str
    ticket: int | None = None
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EaReport:
    """One EA's last heartbeat, its observed state and its journal."""

    symbol: str
    magic: int
    protocol_version: int
    ea_version: str
    heartbeat_at: datetime
    status: EaStatus
    connected: bool
    trade_allowed: bool
    applied_revision: int
    kill_switch: bool
    local_halt: bool
    halt_reason: str
    state_age_seconds: float | None
    positions: tuple[ReportedPosition, ...]
    events: tuple[EaEvent, ...]
    counters: Mapping[str, Any] = field(default_factory=dict)
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EaDivergence:
    """One difference between what the backend expects and what the EA reports.

    The same three fields as :class:`tradingagent.execution.reconciliation.Divergence`, so
    the operator reads one shape everywhere. Nothing is ever corrected automatically
    (RM-014): a divergence is a reason to stop and look.
    """

    kind: str
    ticket: int | None
    detail: str


def state_path(directory: Path | str, symbol: str) -> Path:
    """Where the state of `symbol` lives, under the configurable bridge directory."""
    return Path(directory) / STATE_DIR_NAME / f"{symbol}{STATE_SUFFIX}"


def report_path(reports_dir: Path | str, symbol: str) -> Path:
    """Where the EA's report of `symbol` lives."""
    return Path(reports_dir) / f"{symbol}{REPORT_SUFFIX}"


def events_path(reports_dir: Path | str, symbol: str) -> Path:
    """Where the EA appends its event journal."""
    return Path(reports_dir) / f"{symbol}{EVENTS_SUFFIX}"


def format_utc(moment: datetime) -> str:
    """ISO-8601 in UTC. A naive datetime is a caller bug and is refused, not guessed."""
    if moment.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware (UTC)")
    return moment.astimezone(UTC).isoformat()


def parse_utc(value: Any) -> datetime:
    """Parse an ISO-8601 stamp into an aware UTC datetime. Raises on anything else."""
    if not isinstance(value, str):
        raise ValueError(f"not an ISO-8601 timestamp: {value!r}")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        # The protocol mandates UTC; an EA build that forgot the offset is still UTC, and
        # treating it as local time would silently shift every staleness verdict.
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _write_atomic(path: Path, text: str) -> None:
    """Write `text` to `path` atomically: temp file in the same directory, then replace.

    A reader either sees the whole previous document or the whole new one, never a
    half-written one â€” which is what makes a torn read impossible even though two
    processes share these files.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def read_json(path: Path) -> dict[str, Any] | None:
    """The document, or None when the file is absent, unreadable, or not an object.

    Absent and corrupt are deliberately the same answer: both mean "nothing usable here",
    and neither may ever raise into the agent's loop.
    """
    try:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def publish_state(
    directory: Path | str,
    symbol: str,
    *,
    magic: int,
    positions: Sequence[ExpectedPosition] = (),
    orders: Sequence[AuthorisedOrder] = (),
    kill_switch: bool = False,
    max_positions: int = 1,
    max_total_volume: float | None = None,
    revision: int | None = None,
    now: Callable[[], datetime] = _utc_now,
) -> Path:
    """Publish one EA's expected state and kill switch, atomically, and return the path.

    `revision` defaults to one more than the revision already on disk, so a caller that
    publishes on a timer does not have to track it. The heartbeat is `now()` in UTC: the
    EA compares it with its own clock and refuses to trade when the backend has gone
    quiet (RM-013).
    """
    target = state_path(directory, symbol)
    if revision is None:
        previous = read_json(target)
        previous_revision = previous.get("revision") if previous else None
        revision = previous_revision + 1 if isinstance(previous_revision, int) else 1
    state = EaState(
        symbol=symbol,
        magic=magic,
        revision=revision,
        published_at=now(),
        kill_switch=kill_switch,
        max_positions=max_positions,
        max_total_volume=max_total_volume,
        positions=tuple(positions),
        orders=tuple(orders),
    )
    _write_atomic(
        target,
        # Pure ASCII: MQL5 has no UTF-8 text mode, so anything outside ASCII travels as a
        # \uXXXX escape and the EA reads the file unchanged with FILE_ANSI.
        json.dumps(state.as_json(), ensure_ascii=True, indent=2),
    )
    return target


def _direction(value: Any) -> Direction | None:
    if not isinstance(value, str):
        return None
    try:
        return Direction(value.upper())
    except ValueError:
        return None


def _float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return default


def _position_from_json(item: Any) -> ReportedPosition | None:
    if not isinstance(item, dict):
        return None
    ticket = _int(item.get("ticket"), 0)
    if ticket <= 0:
        return None
    return ReportedPosition(
        ticket=ticket,
        symbol=str(item.get("symbol", "")),
        direction=_direction(item.get("direction")),
        volume=_float(item.get("volume")),
        price_open=_float(item.get("price_open")),
        stop_loss=_float(item.get("stop_loss")),
        take_profit=_float(item.get("take_profit")),
        profit=_float(item.get("profit")),
        comment=str(item.get("comment", "")),
    )


def _event_from_json(item: Any) -> EaEvent | None:
    if not isinstance(item, dict):
        return None
    try:
        at = parse_utc(item.get("at"))
    except ValueError:
        return None
    data = item.get("data")
    return EaEvent(
        seq=_int(item.get("seq")),
        at=at,
        kind=str(item.get("kind", "")),
        severity=str(item.get("severity", "")),
        message=str(item.get("message", "")),
        ticket=_int(item["ticket"]) if isinstance(item.get("ticket"), int) else None,
        data=data if isinstance(data, dict) else {},
    )


def _report_from_payload(payload: Mapping[str, Any], fallback_symbol: str) -> EaReport:
    """Build a report from a parsed document. Raises ValueError when the heartbeat is absent."""
    heartbeat_at = parse_utc(payload.get("updated_at"))
    positions = payload.get("positions")
    events = payload.get("events")
    counters = payload.get("counters")
    state_age = payload.get("state_age_seconds")
    symbol = payload.get("symbol")
    return EaReport(
        symbol=symbol if isinstance(symbol, str) and symbol else fallback_symbol,
        magic=_int(payload.get("magic")),
        protocol_version=_int(payload.get("protocol_version")),
        ea_version=str(payload.get("ea_version", "")),
        heartbeat_at=heartbeat_at,
        status=EaStatus.ONLINE,
        connected=bool(payload.get("connected", False)),
        trade_allowed=bool(payload.get("trade_allowed", False)),
        applied_revision=_int(payload.get("applied_revision")),
        kill_switch=bool(payload.get("kill_switch", False)),
        local_halt=bool(payload.get("local_halt", False)),
        halt_reason=str(payload.get("halt_reason", "")),
        state_age_seconds=(
            float(state_age) if isinstance(state_age, (int, float)) and state_age >= 0 else None
        ),
        positions=tuple(
            position
            for position in (_position_from_json(item) for item in _as_list(positions))
            if position is not None
        ),
        events=tuple(
            event
            for event in (_event_from_json(item) for item in _as_list(events))
            if event is not None
        ),
        counters=dict(counters) if isinstance(counters, dict) else {},
        raw=dict(payload),
    )


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def status_of(
    heartbeat_at: datetime,
    *,
    now: Callable[[], datetime] | None = None,
    timeout_seconds: float = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
) -> EaStatus:
    """ONLINE when the last heartbeat is younger than the timeout, OFFLINE otherwise."""
    reference = now() if now is not None else _utc_now()
    if (reference - heartbeat_at).total_seconds() < timeout_seconds:
        return EaStatus.ONLINE
    return EaStatus.OFFLINE


def read_reports(
    reports_dir: Path | str,
    *,
    now: Callable[[], datetime] = _utc_now,
    timeout_seconds: float = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
) -> tuple[EaReport, ...]:
    """Every parseable report, oldest symbol first. Corrupt or missing files are skipped.

    The caller that must also *see* an EA whose file is corrupt uses
    :func:`tradingagent.ea.health.ea_health`, which keys on the file name instead.
    """
    directory = Path(reports_dir)
    if not directory.is_dir():
        return ()
    reports: list[EaReport] = []
    for path in sorted(directory.glob(f"*{REPORT_SUFFIX}")):
        fallback = path.name[: -len(REPORT_SUFFIX)]
        payload = read_json(path)
        if payload is None:
            continue
        try:
            report = _report_from_payload(payload, fallback)
        except ValueError:
            continue
        reports.append(_with_status(report, now=now, timeout_seconds=timeout_seconds))
    return tuple(reports)


def _with_status(
    report: EaReport,
    *,
    now: Callable[[], datetime],
    timeout_seconds: float,
) -> EaReport:
    return replace(
        report,
        status=status_of(report.heartbeat_at, now=now, timeout_seconds=timeout_seconds),
    )


def read_events(reports_dir: Path | str, symbol: str) -> tuple[EaEvent, ...]:
    """The EA's append-only journal for one symbol; unreadable lines are skipped."""
    path = events_path(reports_dir, symbol)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ()
    events: list[EaEvent] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        event = _event_from_json(payload)
        if event is not None:
            events.append(event)
    return tuple(events)


def compare_expected(
    expected: EaState,
    report: EaReport,
    *,
    volume_tolerance: float = 1e-6,
    price_tolerance: float = 0.01,
) -> tuple[EaDivergence, ...]:
    """Expected state against the EA's report, both directions, like RM-014.

    This is a second pair of eyes on top of the EA's own watchdog: the EA compares what it
    sees with the last state it read, the agent compares that report with what it last
    published. Nothing is corrected here either â€” a divergence is a halt and an alert.
    """
    found: list[EaDivergence] = []
    if expected.symbol != report.symbol:
        found.append(
            EaDivergence("symbol", None, f"expected {expected.symbol}, EA reports {report.symbol}")
        )
    if expected.magic != report.magic:
        found.append(
            EaDivergence("magic", None, f"expected magic {expected.magic}, EA uses {report.magic}")
        )
    if report.applied_revision != expected.revision:
        found.append(
            EaDivergence(
                "revision",
                None,
                f"EA applied revision {report.applied_revision}, "
                f"the published state is {expected.revision}",
            )
        )
    local = {position.ticket: position for position in expected.positions}
    remote = {position.ticket: position for position in report.positions}
    for ticket in sorted(set(local) - set(remote)):
        expected_position = local[ticket]
        found.append(
            EaDivergence(
                "missing_in_terminal",
                ticket,
                f"expected {expected_position.direction.value} "
                f"{expected_position.volume} {expected.symbol} is absent from the terminal",
            )
        )
    for ticket in sorted(set(remote) - set(local)):
        seen = remote[ticket]
        found.append(
            EaDivergence(
                "unknown_in_terminal",
                ticket,
                f"position {ticket} {seen.symbol} is open in the terminal but not expected",
            )
        )
    for ticket in sorted(set(local) & set(remote)):
        found.extend(
            _compare_position(local[ticket], remote[ticket], volume_tolerance, price_tolerance)
        )
    return tuple(found)


def _compare_position(
    expected: ExpectedPosition,
    seen: ReportedPosition,
    volume_tolerance: float,
    price_tolerance: float,
) -> list[EaDivergence]:
    found: list[EaDivergence] = []
    if seen.direction is not None and seen.direction is not expected.direction:
        found.append(
            EaDivergence(
                "side",
                expected.ticket,
                f"position {expected.ticket} is {expected.direction.value} expected, "
                f"{seen.direction.value} in the terminal",
            )
        )
    if abs(expected.volume - seen.volume) > volume_tolerance:
        found.append(
            EaDivergence(
                "volume",
                expected.ticket,
                f"position {expected.ticket} volume {expected.volume} expected, "
                f"{seen.volume} in the terminal",
            )
        )
    if not _same_stop(expected.stop_loss, seen.stop_loss, price_tolerance):
        found.append(
            EaDivergence(
                "stop",
                expected.ticket,
                f"position {expected.ticket} stop {expected.stop_loss} expected, "
                f"{seen.stop_loss or None} in the terminal",
            )
        )
    return found


def _same_stop(expected: float | None, seen: float, tolerance: float) -> bool:
    if expected is None:
        return seen == 0
    if seen == 0:
        return False
    return abs(expected - seen) <= tolerance


def newest_events(reports: Iterable[EaReport], *, limit: int = 20) -> tuple[EaEvent, ...]:
    """The most recent events across reports, worst first for an operator's glance."""
    everything = [event for report in reports for event in report.events]
    everything.sort(key=lambda event: (event.at, event.seq), reverse=True)
    return tuple(everything[:limit])


__all__ = [
    "DEFAULT_HEARTBEAT_TIMEOUT_SECONDS",
    "EVENTS_SUFFIX",
    "PROTOCOL_VERSION",
    "REPORTS_DIR_NAME",
    "REPORT_SUFFIX",
    "STATE_DIR_NAME",
    "STATE_SUFFIX",
    "AuthorisedOrder",
    "EaDivergence",
    "EaEvent",
    "EaReport",
    "EaState",
    "EaStatus",
    "ExpectedPosition",
    "OrderAction",
    "ReportedPosition",
    "compare_expected",
    "events_path",
    "format_utc",
    "newest_events",
    "parse_utc",
    "publish_state",
    "read_events",
    "read_json",
    "read_reports",
    "report_path",
    "state_path",
    "status_of",
]
