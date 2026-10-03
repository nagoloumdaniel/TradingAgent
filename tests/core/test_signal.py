import math

import pytest

from tradingagent.core.market import Direction
from tradingagent.core.signal import InvalidSignalError, SignalCandidate

BUY = {
    "direction": Direction.BUY,
    "entry_low": 100.0,
    "entry_high": 101.0,
    "stop_loss": 98.0,
    "take_profits": (104.0, 106.0),
    "reason": "trend continuation",
    "indicators": {"ema": 100.5},
}
SELL = {
    **BUY,
    "direction": Direction.SELL,
    "stop_loss": 103.0,
    "take_profits": (97.0, 95.0),
}


def signal(base: dict[str, object], **overrides: object) -> SignalCandidate:
    return SignalCandidate(**{**base, **overrides})  # type: ignore[arg-type]


@pytest.mark.parametrize("base", [BUY, SELL])
def test_coherent_signal_is_accepted(base: dict[str, object]) -> None:
    assert signal(base).reason == "trend continuation"


@pytest.mark.parametrize(
    ("base", "overrides"),
    [
        (BUY, {"entry_low": 102.0}),
        (BUY, {"stop_loss": 100.0}),
        (BUY, {"stop_loss": 100.5}),
        (BUY, {"take_profits": ()}),
        (BUY, {"take_profits": (101.0,)}),
        (BUY, {"take_profits": (106.0, 104.0)}),
        (BUY, {"take_profits": (104.0, 104.0)}),
        (SELL, {"stop_loss": 101.0}),
        (SELL, {"take_profits": (100.0,)}),
        (SELL, {"take_profits": (95.0, 97.0)}),
        (BUY, {"reason": "   "}),
        (BUY, {"stop_loss": math.nan}),
        (BUY, {"entry_high": math.inf}),
        (BUY, {"take_profits": (math.inf,)}),
        (BUY, {"indicators": {"ema": math.nan}}),
    ],
)
def test_incoherent_signal_is_rejected(
    base: dict[str, object], overrides: dict[str, object]
) -> None:
    with pytest.raises(InvalidSignalError):
        signal(base, **overrides)


def test_invalid_signal_error_is_a_value_error() -> None:
    assert issubclass(InvalidSignalError, ValueError)


def test_indicators_are_copied_and_frozen() -> None:
    source = {"ema": 100.5}
    candidate = signal(BUY, indicators=source)
    source["ema"] = 0.0
    assert candidate.indicators["ema"] == 100.5
    with pytest.raises(TypeError):
        candidate.indicators["ema"] = 1.0  # type: ignore[index]


def test_signal_is_immutable() -> None:
    with pytest.raises(AttributeError):
        signal(BUY).stop_loss = 1.0  # type: ignore[misc]
