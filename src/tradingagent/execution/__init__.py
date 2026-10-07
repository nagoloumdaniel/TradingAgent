"""Exécuteurs (paper et MT5), suivi des positions, réconciliation. Seul `risk` y accède.

The composition root writes `from tradingagent.execution import MT5Broker, PaperBroker`;
the agent loop type-hints against `runtime.ports.BrokerPort`, which these satisfy
structurally. Both concrete brokers expose `async def initialize(self) -> None` and build
no connection in their constructor.
"""

from tradingagent.execution.mt5_broker import MT5Broker, key_comment
from tradingagent.execution.paper_broker import PaperBroker
from tradingagent.execution.ports import (
    Alert,
    Broker,
    BrokerError,
    BrokerUnavailableError,
    OrderRefusedError,
    OrderSnapshot,
    TradeLog,
)
from tradingagent.execution.reconciliation import (
    Divergence,
    Reconciler,
    Reconciliation,
    reconcile_state,
)
from tradingagent.execution.tracking import PositionTracker

__all__ = [
    "Alert",
    "Broker",
    "BrokerError",
    "BrokerUnavailableError",
    "Divergence",
    "MT5Broker",
    "OrderRefusedError",
    "OrderSnapshot",
    "PaperBroker",
    "PositionTracker",
    "Reconciler",
    "Reconciliation",
    "TradeLog",
    "key_comment",
    "reconcile_state",
]
