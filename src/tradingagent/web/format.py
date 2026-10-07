"""Display formatting for the dashboard: pure functions, no database, no clock.

Every string the operator reads is built here, once, so a page and the SSE stream can
never disagree about how a number is written. Nothing in this module computes a figure:
it only renders values that :mod:`tradingagent.analytics` or the storage readers already
produced (§34).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import HaltAction, HaltSource, Severity, SignalState

NA = "n/a"
EURO = "\N{EURO SIGN}"

_DIRECTIONS = {Direction.BUY: "Achat", Direction.SELL: "Vente"}
_MODES = {
    TradingMode.OBSERVATION: "Observation",
    TradingMode.SIGNAL: "Signal",
    TradingMode.PAPER: "Papier",
    TradingMode.DEMO: "Démo",
    TradingMode.LIVE: "Réel",
}
_SEVERITIES = {
    Severity.INFO: "Info",
    Severity.WARNING: "Avertissement",
    Severity.CRITICAL: "Critique",
}
_HALT_ACTIONS = {HaltAction.HALT: "Arrêt", HaltAction.RESUME: "Reprise"}
_HALT_SOURCES = {
    HaltSource.AUTOMATIC: "automatique",
    HaltSource.TELEGRAM: "Telegram",
    HaltSource.SERVER: "serveur",
}
_SIGNAL_STATES = {
    SignalState.CANDIDATE: "Candidat",
    SignalState.RISK_REJECTED: "Refusé par le risque",
    SignalState.VALIDATED: "Validé",
    SignalState.SENT: "Envoyé",
    SignalState.EXPIRED: "Expiré",
    SignalState.ACCEPTED: "Accepté",
    SignalState.IGNORED: "Ignoré",
    SignalState.ORDER_SENT: "Ordre envoyé",
    SignalState.ORDER_ACCEPTED: "Ordre accepté",
    SignalState.ORDER_REJECTED: "Ordre refusé",
    SignalState.POSITION_OPEN: "Position ouverte",
    SignalState.PARTIALLY_CLOSED: "Partiellement clôturé",
    SignalState.CLOSED: "Clôturé",
    SignalState.CANCELLED: "Annulé",
    SignalState.ERROR: "Erreur",
}


def money(value: Decimal | None, *, signed: bool = False) -> str:
    """An exact amount, always two decimals. Never rendered through a float."""
    if value is None:
        return NA
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:.2f} {EURO}"


def number(value: float | Decimal | None, digits: int = 2) -> str:
    if value is None:
        return NA
    return f"{float(value):.{digits}f}"


def ratio(value: float | None) -> str:
    """A dimensionless ratio (profit factor, Sharpe): shown as-is, never re-derived."""
    return NA if value is None else f"{value:.2f}"


def percent(value: Decimal | float | None) -> str:
    """A fraction already expressed as a ratio (0.42 → 42.0 %)."""
    return NA if value is None else f"{float(value) * 100:.1f} %"


def percent_points(value: Decimal | float | None) -> str:
    """A value already expressed in percent (0.5 → 0.5 %), as the YAML states limits."""
    return NA if value is None else f"{float(value):.1f} %"


def duration(value: timedelta | None) -> str:
    if value is None:
        return NA
    seconds = int(value.total_seconds())
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{sign}{days} j {hours:02d}:{minutes:02d}"
    if hours:
        return f"{sign}{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{sign}{minutes:02d}:{secs:02d}"


def moment(value: datetime | None) -> str:
    """UTC, minute precision: the dashboard never shows a local time it did not record."""
    if value is None:
        return NA
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def precise(value: datetime | None) -> str:
    if value is None:
        return NA
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def direction_label(direction: Direction) -> str:
    return _DIRECTIONS.get(direction, str(direction))


def mode_label(mode: TradingMode) -> str:
    return _MODES.get(mode, str(mode))


def severity_label(severity: Severity) -> str:
    return _SEVERITIES.get(severity, str(severity))


def halt_action_label(action: HaltAction) -> str:
    return _HALT_ACTIONS.get(action, str(action))


def halt_source_label(source: HaltSource) -> str:
    return _HALT_SOURCES.get(source, str(source))


def signal_state_label(state: SignalState) -> str:
    return _SIGNAL_STATES.get(state, str(state))


def severity_class(severity: Severity) -> str:
    return {"info": "ok", "warning": "warn", "critical": "bad"}.get(str(severity), "ok")


__all__ = [
    "NA",
    "direction_label",
    "duration",
    "halt_action_label",
    "halt_source_label",
    "mode_label",
    "moment",
    "money",
    "number",
    "percent",
    "percent_points",
    "precise",
    "ratio",
    "severity_class",
    "severity_label",
    "signal_state_label",
]
