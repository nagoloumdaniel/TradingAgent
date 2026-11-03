from enum import StrEnum


class TradingMode(StrEnum):
    """The two trading modes: DEMO (demonstration account) and LIVE (réel, real money)."""

    DEMO = "DEMO"
    LIVE = "LIVE"


_RANK = {mode: rank for rank, mode in enumerate(TradingMode)}

#: Values older databases may still hold. They are history, never produced any more, and are
#: read back as DEMO (nothing but the demonstration account ever carried them).
LEGACY_MODE_VALUES = frozenset({"OBSERVATION", "SIGNAL", "PAPER"})


def mode_rank(mode: TradingMode) -> int:
    """Exposure order: DEMO < LIVE."""
    return _RANK[mode]


class AiFilter(StrEnum):
    SHADOW = "shadow"
    ADVISORY = "advisory"
    REQUIRED = "required"
