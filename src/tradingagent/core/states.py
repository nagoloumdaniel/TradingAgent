from enum import StrEnum


class SignalState(StrEnum):
    """Signal lifecycle, RM-018. Allowed transitions are enforced in TASK-040."""

    CANDIDATE = "candidate"
    RISK_REJECTED = "risk_rejected"
    VALIDATED = "validated"
    SENT = "sent"
    EXPIRED = "expired"
    ACCEPTED = "accepted"
    IGNORED = "ignored"
    ORDER_SENT = "order_sent"
    ORDER_ACCEPTED = "order_accepted"
    ORDER_REJECTED = "order_rejected"
    POSITION_OPEN = "position_open"
    PARTIALLY_CLOSED = "partially_closed"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    ERROR = "error"


class OrderState(StrEnum):
    SENT = "sent"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FILLED = "filled"
    CANCELLED = "cancelled"
    ERROR = "error"


class PositionState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class RiskOutcome(StrEnum):
    AUTHORIZED = "authorized"
    REDUCED = "reduced"
    REFUSED = "refused"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class HaltAction(StrEnum):
    HALT = "halt"
    RESUME = "resume"


class HaltSource(StrEnum):
    """Who issued a halt command. Only an operator source may lift a global halt."""

    AUTOMATIC = "automatic"
    TELEGRAM = "telegram"
    SERVER = "server"


class StrategyStatus(StrEnum):
    """Strategy lifecycle (cahier v3 §14). Only LIVE may trade; LIVE is immutable, any
    change produces a new version with its own reference."""

    DISCOVERED = "discovered"
    EXPERIMENTAL = "experimental"
    BACKTESTING = "backtesting"
    VALIDATING = "validating"
    PAPER = "paper"
    CANDIDATE = "candidate"
    LIVE = "live"
    DEPRECATED = "deprecated"


ALLOWED_STATUS_TRANSITIONS: dict[StrategyStatus, frozenset[StrategyStatus]] = {
    StrategyStatus.DISCOVERED: frozenset({StrategyStatus.EXPERIMENTAL, StrategyStatus.DEPRECATED}),
    StrategyStatus.EXPERIMENTAL: frozenset({StrategyStatus.BACKTESTING, StrategyStatus.DEPRECATED}),
    StrategyStatus.BACKTESTING: frozenset({StrategyStatus.VALIDATING, StrategyStatus.DEPRECATED}),
    StrategyStatus.VALIDATING: frozenset({StrategyStatus.PAPER, StrategyStatus.DEPRECATED}),
    StrategyStatus.PAPER: frozenset({StrategyStatus.CANDIDATE, StrategyStatus.DEPRECATED}),
    StrategyStatus.CANDIDATE: frozenset({StrategyStatus.LIVE, StrategyStatus.DEPRECATED}),
    StrategyStatus.LIVE: frozenset({StrategyStatus.DEPRECATED}),
    StrategyStatus.DEPRECATED: frozenset(),
}


class ValidationStage(StrEnum):
    """The gates a candidate must clear before promotion (cahier v3 §10, §49)."""

    BACKTEST = "backtest"
    COSTS = "costs"
    WALK_FORWARD = "walk_forward"
    OUT_OF_SAMPLE = "out_of_sample"
    MONTE_CARLO = "monte_carlo"
    STRESS = "stress"
    PARAMETER_ROBUSTNESS = "parameter_robustness"
    PAPER = "paper"
    RISK = "risk"


class AnalysisKind(StrEnum):
    """What an AI analysis is about. It never decides: it informs (cahier v3 §5, §39)."""

    LOSS_ANALYSIS = "loss_analysis"
    DEGRADATION = "degradation"
    REGIME = "regime"
    HYPOTHESIS = "hypothesis"
    POSTMORTEM = "postmortem"


class ProposalStatus(StrEnum):
    PROPOSED = "proposed"
    VALIDATING = "validating"
    REJECTED = "rejected"
    PROMOTED = "promoted"


class ExecutionEventKind(StrEnum):
    """Execution telemetry (cahier v3 §20, §47): measured at every hop, never guessed."""

    SIGNAL_GENERATED = "signal_generated"
    ORDER_REQUESTED = "order_requested"
    ORDER_SENT = "order_sent"
    ORDER_ACCEPTED = "order_accepted"
    ORDER_REJECTED = "order_rejected"
    FILLED = "filled"
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    STOP_MISSING = "stop_missing"
    ERROR = "error"
