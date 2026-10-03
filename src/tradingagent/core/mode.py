from enum import StrEnum


class TradingMode(StrEnum):
    OBSERVATION = "OBSERVATION"
    SIGNAL = "SIGNAL"
    PAPER = "PAPER"
    DEMO = "DEMO"
    LIVE = "LIVE"


_RANK = {mode: rank for rank, mode in enumerate(TradingMode)}


def mode_rank(mode: TradingMode) -> int:
    """Exposure order: OBSERVATION < SIGNAL < PAPER < DEMO < LIVE."""
    return _RANK[mode]


class AiFilter(StrEnum):
    SHADOW = "shadow"
    ADVISORY = "advisory"
    REQUIRED = "required"
