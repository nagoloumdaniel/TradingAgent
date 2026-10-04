"""Halt state shared by the risk engine, the executors and the operator tools (F-019).

Scopes: `global` (operator decisions and hard limits), `connection` (lifted automatically
once data is healthy again, RM-013) and one scope per (strategy, market) quarantine.
"""

from dataclasses import dataclass

GLOBAL = "global"
CONNECTION = "connection"
TRADING_SCOPES = (GLOBAL, CONNECTION)
PAIR_PREFIX = "pair:"


def pair_scope(ref: str, symbol: str) -> str:
    return f"{PAIR_PREFIX}{ref}:{symbol}"


def parse_pair_scope(scope: str) -> tuple[str, str]:
    if not scope.startswith(PAIR_PREFIX):
        raise ValueError(f"{scope!r} is not a strategy and market scope")
    ref, _, symbol = scope.removeprefix(PAIR_PREFIX).rpartition(":")
    if not ref or not symbol:
        raise ValueError(f"malformed pair scope {scope!r}")
    return ref, symbol


@dataclass(frozen=True)
class HaltStatus:
    halted: bool
    close_positions: bool = False
    reasons: tuple[str, ...] = ()


TRADING = HaltStatus(halted=False)
