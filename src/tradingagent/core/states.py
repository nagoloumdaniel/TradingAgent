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
