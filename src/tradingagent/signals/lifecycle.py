"""The signal state machine (F-020, RM-018, TASK-040).

Fifteen states, and the only edges between them. A signal's life: the risk layer decides
(CANDIDATE → VALIDATED or RISK_REJECTED), the operator or an executor answers (SENT →
ACCEPTED or IGNORED), the order layer talks to the broker (ORDER_SENT → ORDER_ACCEPTED or
ORDER_REJECTED), the position follows to its close. EXPIRED is reachable whenever the
offer is still alive; ERROR records infrastructure failures from any live state. Terminal
states have no exits: history is never rewritten, only extended (append-only triggers).
"""

from tradingagent.core.states import SignalState


class IllegalTransitionError(ValueError):
    """The requested state change is not part of RM-018."""


TRANSITIONS: dict[SignalState, frozenset[SignalState]] = {
    SignalState.CANDIDATE: frozenset(
        {SignalState.VALIDATED, SignalState.RISK_REJECTED, SignalState.EXPIRED, SignalState.ERROR}
    ),
    SignalState.VALIDATED: frozenset(
        {SignalState.SENT, SignalState.EXPIRED, SignalState.CANCELLED, SignalState.ERROR}
    ),
    SignalState.SENT: frozenset(
        {
            SignalState.ACCEPTED,
            SignalState.IGNORED,
            SignalState.EXPIRED,
            SignalState.CANCELLED,
            SignalState.ERROR,
        }
    ),
    SignalState.ACCEPTED: frozenset(
        {SignalState.ORDER_SENT, SignalState.CANCELLED, SignalState.ERROR}
    ),
    SignalState.ORDER_SENT: frozenset(
        {SignalState.ORDER_ACCEPTED, SignalState.ORDER_REJECTED, SignalState.ERROR}
    ),
    SignalState.ORDER_ACCEPTED: frozenset({SignalState.POSITION_OPEN, SignalState.ERROR}),
    SignalState.POSITION_OPEN: frozenset(
        {SignalState.PARTIALLY_CLOSED, SignalState.CLOSED, SignalState.ERROR}
    ),
    SignalState.PARTIALLY_CLOSED: frozenset(
        {SignalState.PARTIALLY_CLOSED, SignalState.CLOSED, SignalState.ERROR}
    ),
    # Terminal states are absent: RISK_REJECTED, EXPIRED, IGNORED, ORDER_REJECTED,
    # CLOSED, CANCELLED, ERROR.
}


def validate_transition(current: SignalState, target: SignalState) -> None:
    if target not in TRANSITIONS.get(current, frozenset()):
        raise IllegalTransitionError(f"{current.value} → {target.value} is not allowed (RM-018)")
