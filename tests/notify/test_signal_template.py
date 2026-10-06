from datetime import UTC, datetime, timedelta

import pytest

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.notify.signal_template import SignalNotice, render_signal_message

GENERATED = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def a_notice(**overrides: object) -> SignalNotice:
    values: dict[str, object] = {
        "ref": "witness@1.0.0:XAUUSD:M15:2026-10-06T12:00Z",
        "symbol": "XAUUSD",
        "direction": Direction.BUY,
        "observed_price": 2650.10,
        "entry_low": 2649.50,
        "entry_high": 2650.50,
        "stop_loss": 2647.50,
        "take_profits": (2652.50, 2655.50),
        "timeframe": Timeframe.M15,
        "strategy_ref": "witness@1.0.0",
        "generated_at": GENERATED,
        "expires_at": GENERATED + timedelta(minutes=45),
        "mode": TradingMode.DEMO,
        "market_state": "HEALTHY",
        "reason": "MM20 crossed above MM50",
        "confidence": None,
    }
    values.update(overrides)
    return SignalNotice(**values)  # type: ignore[arg-type]


REQUIRED_PARTS = {
    "unique identifier": "witness@1.0.0:XAUUSD:M15:2026-10-06T12:00Z",
    "market": "XAUUSD",
    "direction": "ACHAT",
    "observed price": "2650.10",
    "entry zone low": "2649.50",
    "entry zone high": "2650.50",
    "stop-loss": "2647.50",
    "first target": "2652.50",
    "second target": "2655.50",
    "timeframe": "M15",
    "strategy and version": "witness@1.0.0",
    "generation time": "2026-10-06 12:00",
    "expiry time": "2026-10-06 12:45",
    "market state": "HEALTHY",
    "justification": "MM20 crossed above MM50",
    "risk reward header": "risque/rendement",
}


@pytest.mark.parametrize(("part", "expected"), sorted(REQUIRED_PARTS.items()))
def test_every_required_field_is_present(part: str, expected: str) -> None:
    assert expected in render_signal_message(a_notice())


@pytest.mark.parametrize(
    ("mode", "must_have", "must_not_have"),
    [
        (TradingMode.DEMO, "DÉMO", "RÉEL"),
        (TradingMode.LIVE, "RÉEL", "DÉMO"),
    ],
)
def test_the_mode_is_unmistakable(mode: TradingMode, must_have: str, must_not_have: str) -> None:
    message = render_signal_message(a_notice(mode=mode))
    assert must_have in message
    assert must_not_have not in message


def test_a_sell_direction_is_named_vente() -> None:
    message = render_signal_message(a_notice(direction=Direction.SELL))
    assert "VENTE" in message


def test_the_risk_reward_ratio_is_estimated_from_the_first_target() -> None:
    # Buy: mid entry 2650.00, stop 2647.50 (risk 2.50), first target 2652.50 (reward 2.50).
    assert "1.0" in render_signal_message(a_notice())


def test_the_risk_reward_ratio_of_a_sell() -> None:
    # Sell: mid entry 20.00, stop 20.50 (risk 0.50), first target 19.00 (reward 1.00).
    message = render_signal_message(
        a_notice(
            symbol="BTCUSD",
            direction=Direction.SELL,
            observed_price=20.10,
            entry_low=19.50,
            entry_high=20.50,
            stop_loss=20.50,
            take_profits=(19.00, 18.00),
        )
    )
    assert "2.0" in message


def test_confidence_is_shown_only_when_it_exists() -> None:
    without = render_signal_message(a_notice())
    assert "confiance" not in without.lower()
    with_confidence = render_signal_message(a_notice(confidence=0.72))
    assert "72 %" in with_confidence


def test_data_derived_text_cannot_break_the_html_formatting() -> None:
    message = render_signal_message(a_notice(reason='MM20 crossed <b>above</b> MM50 & "left"'))
    assert "&lt;b&gt;above&lt;/b&gt;" in message
    assert "&amp;" in message
    assert "<b>above</b>" not in message


def test_times_are_explicitly_utc() -> None:
    message = render_signal_message(a_notice())
    assert message.count("UTC") >= 2
