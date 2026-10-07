"""Bridge between the Python agent and the MQL5 Guardian Expert Advisors.

The EAs are never the brain: they execute the orders the backend authorises, watch the
account, protect the stops and report what happened (see `docs/ea/protocole-pont.md`).
This package is the Python side of that contract and imports no terminal at all, so it
runs — and is tested — without MetaTrader 5.
"""

from tradingagent.ea.bridge import (
    DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
    PROTOCOL_VERSION,
    AuthorisedOrder,
    EaDivergence,
    EaEvent,
    EaReport,
    EaState,
    EaStatus,
    ExpectedPosition,
    OrderAction,
    ReportedPosition,
    compare_expected,
    publish_state,
    read_events,
    read_reports,
    report_path,
    state_path,
)
from tradingagent.ea.health import ea_health

__all__ = [
    "DEFAULT_HEARTBEAT_TIMEOUT_SECONDS",
    "PROTOCOL_VERSION",
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
    "ea_health",
    "publish_state",
    "read_events",
    "read_reports",
    "report_path",
    "state_path",
]
