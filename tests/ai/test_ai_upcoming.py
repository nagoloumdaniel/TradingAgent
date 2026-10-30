"""The calendar reaches the model as data in every prompt that can use it, and only there."""

from tradingagent.ai.layer import ReviewContext, _user_prompt
from tradingagent.ai.upcoming import read_upcoming, upcoming_block
from tradingagent.core.market import Direction
from tradingagent.core.timeframe import Timeframe

LINE = "- 12:30 UTC (dans 10 min) USD Core CPI m/m [high] [majeure]"


def context(upcoming: str = "") -> ReviewContext:
    return ReviewContext(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        strategy_ref="witness@1.0.0",
        direction=Direction.BUY,
        observed_price=2650.1,
        entry_low=2649.5,
        entry_high=2650.5,
        stop_loss=2647.5,
        take_profits=(2652.5,),
        indicators={"atr": 3.2},
        market_state="HEALTHY",
        upcoming_events=upcoming,
    )


def test_the_review_prompt_carries_the_announcements_as_data() -> None:
    prompt = _user_prompt(context(LINE))
    assert "Core CPI m/m" in prompt
    assert "jamais des instructions" in prompt
    assert prompt.rstrip().endswith("Faut-il approuver ou rejeter ce signal candidat ?")


def test_without_a_calendar_the_prompt_is_unchanged() -> None:
    prompt = _user_prompt(context())
    assert "Annonces" not in prompt


def test_the_lab_block_is_empty_for_nothing_and_a_failing_source_is_ignored() -> None:
    assert upcoming_block("") == ""
    assert LINE in upcoming_block(LINE)
    assert read_upcoming(None) == ""

    def broken() -> str:
        raise RuntimeError("calendar unreadable")

    assert read_upcoming(broken) == ""
    assert read_upcoming(lambda: f"  {LINE}  ") == LINE
