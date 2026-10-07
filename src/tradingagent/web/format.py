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
from tradingagent.core.states import (
    AnalysisKind,
    ExecutionEventKind,
    HaltAction,
    HaltSource,
    OrderState,
    PositionState,
    RiskOutcome,
    Severity,
    SignalState,
    StrategyStatus,
)

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
_ORDER_STATES = {
    OrderState.SENT: "Envoyé",
    OrderState.ACCEPTED: "Accepté",
    OrderState.REJECTED: "Refusé",
    OrderState.FILLED: "Exécuté",
    OrderState.CANCELLED: "Annulé",
    OrderState.ERROR: "Erreur",
}
_POSITION_STATES = {PositionState.OPEN: "Ouverte", PositionState.CLOSED: "Clôturée"}
_RISK_OUTCOMES = {
    RiskOutcome.AUTHORIZED: "Autorisé",
    RiskOutcome.REDUCED: "Réduit",
    RiskOutcome.REFUSED: "Refusé",
}
_EXECUTION_KINDS = {
    ExecutionEventKind.SIGNAL_GENERATED: "Signal généré",
    ExecutionEventKind.ORDER_REQUESTED: "Ordre demandé",
    ExecutionEventKind.ORDER_SENT: "Ordre envoyé",
    ExecutionEventKind.ORDER_ACCEPTED: "Ordre accepté",
    ExecutionEventKind.ORDER_REJECTED: "Ordre refusé",
    ExecutionEventKind.FILLED: "Exécuté",
    ExecutionEventKind.POSITION_OPENED: "Position ouverte",
    ExecutionEventKind.POSITION_CLOSED: "Position clôturée",
    ExecutionEventKind.STOP_MISSING: "Stop manquant",
    ExecutionEventKind.ERROR: "Erreur",
}
_ANALYSIS_KINDS = {
    AnalysisKind.LOSS_ANALYSIS: "Analyse de perte",
    AnalysisKind.DEGRADATION: "Dégradation",
    AnalysisKind.REGIME: "Régime de marché",
    AnalysisKind.HYPOTHESIS: "Hypothèse",
    AnalysisKind.POSTMORTEM: "Post-mortem",
}
_STRATEGY_STATUSES = {
    StrategyStatus.DISCOVERED: "Découverte",
    StrategyStatus.EXPERIMENTAL: "Expérimentale",
    StrategyStatus.BACKTESTING: "Backtest",
    StrategyStatus.VALIDATING: "Validation",
    StrategyStatus.PAPER: "Papier",
    StrategyStatus.CANDIDATE: "Candidate",
    StrategyStatus.LIVE: "Live",
    StrategyStatus.DEPRECATED: "Retirée",
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
# The vocabulary of `ai.daily`: the labels are its own, this table only reads them in the
# operator's language. Anything unknown falls back to the stored string, never to a guess.
_REGIMES = {
    "volatilite_haute": "Volatilité haute",
    "volatilite_normale": "Volatilité normale",
    "volatilite_basse": "Volatilité basse",
    "inconnu": "Inconnu",
}
_SESSIONS = {
    "asie": "Asie",
    "londres": "Londres",
    "new_york": "New York",
    "apres_cloture": "Après clôture",
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


def order_state_label(state: str) -> str:
    """Order states arrive as their stored value; an unknown one is shown as stored."""
    try:
        return _ORDER_STATES.get(OrderState(state), state)
    except ValueError:
        return state


def position_state_label(state: str) -> str:
    try:
        return _POSITION_STATES.get(PositionState(state), state)
    except ValueError:
        return state


def risk_outcome_label(outcome: RiskOutcome | str) -> str:
    try:
        parsed = RiskOutcome(outcome)
    except ValueError:
        return str(outcome)
    return _RISK_OUTCOMES.get(parsed, str(outcome))


def execution_kind_label(kind: ExecutionEventKind | str) -> str:
    try:
        parsed = ExecutionEventKind(kind)
    except ValueError:
        return str(kind)
    return _EXECUTION_KINDS.get(parsed, str(kind))


def analysis_kind_label(kind: AnalysisKind | str) -> str:
    try:
        parsed = AnalysisKind(kind)
    except ValueError:
        return str(kind)
    return _ANALYSIS_KINDS.get(parsed, str(kind))


def strategy_status_label(status: StrategyStatus | str) -> str:
    try:
        parsed = StrategyStatus(status)
    except ValueError:
        return str(status)
    return _STRATEGY_STATUSES.get(parsed, str(status))


def regime_label(regime: str | None) -> str:
    """The volatility regime as `ai.daily.regime_of` named it. Absent stays absent."""
    return NA if regime is None else _REGIMES.get(regime, regime)


def session_label(session: str | None) -> str:
    """The UTC session as `ai.daily.session_of` named it, read in French."""
    return NA if session is None else _SESSIONS.get(session, session)


def r_multiple(value: Decimal | float | None) -> str:
    """Realized R, written the way the storage layer defines it (profit over risk)."""
    return NA if value is None else f"{float(value):+.2f} R"


def milliseconds(value: int | None) -> str:
    """A measured hop latency. Absent stays absent: no zero is invented."""
    return NA if value is None else f"{value} ms"


def ratio_class(value: Decimal | float | None) -> str:
    if value is None:
        return ""
    return "pos" if value > 0 else ("neg" if value < 0 else "")


def severity_class(severity: Severity) -> str:
    return {"info": "ok", "warning": "warn", "critical": "bad"}.get(str(severity), "ok")


__all__ = [
    "NA",
    "analysis_kind_label",
    "direction_label",
    "duration",
    "execution_kind_label",
    "halt_action_label",
    "halt_source_label",
    "milliseconds",
    "mode_label",
    "moment",
    "money",
    "number",
    "order_state_label",
    "percent",
    "percent_points",
    "position_state_label",
    "precise",
    "r_multiple",
    "ratio",
    "ratio_class",
    "regime_label",
    "risk_outcome_label",
    "session_label",
    "severity_class",
    "severity_label",
    "signal_state_label",
    "strategy_status_label",
]
