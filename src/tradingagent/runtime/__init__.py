"""Agent runtime: the loop that ties data, strategies, risk, notification and execution.

This package never imports `execution`: it drives an executor through the structural
protocols of `runtime.ports`. The composition root, `tradingagent.app`, is the only place
where the concrete broker is chosen and injected.
"""

from tradingagent.runtime.loop import AgentLoop, CycleReport
from tradingagent.runtime.pipeline import ProcessOutcome, SignalPipeline
from tradingagent.runtime.portfolio import PortfolioBuilder, day_start, week_start
from tradingagent.runtime.ports import BrokerPort, MarketPort, NotifierPort

__all__ = [
    "AgentLoop",
    "BrokerPort",
    "CycleReport",
    "MarketPort",
    "NotifierPort",
    "PortfolioBuilder",
    "ProcessOutcome",
    "SignalPipeline",
    "day_start",
    "week_start",
]
