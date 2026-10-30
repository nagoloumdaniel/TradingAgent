import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradingagent.news.analyst import (
    INFLUENCE_LEEWAY,
    PURPOSE_BRIEF,
    PURPOSE_EVENTS,
    NewsAnalyst,
)
from tradingagent.news.config import NewsConfig
from tradingagent.news.model import (
    BTCUSD,
    XAUUSD,
    Bias,
    EconomicEvent,
    EventKind,
    Impact,
    MarketState,
    Stance,
    event_key,
    influence_score,
)
from tradingagent.storage.ai_calls import AiCall, AiReply

NOW = datetime(2026, 10, 30, 6, 0, tzinfo=UTC)
AT = datetime(2026, 10, 30, 12, 30, tzinfo=UTC)
UP = {
    XAUUSD: MarketState(XAUUSD, "up", "normal", 4000.0, NOW),
    BTCUSD: MarketState(BTCUSD, "up", "normal", 80000.0, NOW),
}


def make(title: str = "Core CPI m/m", at: datetime = AT, currency: str = "USD") -> EconomicEvent:
    return EconomicEvent(
        key=event_key("t", currency, title, at),
        starts_at=at,
        currency=currency,
        title=title,
        impact=Impact.HIGH,
        kind=EventKind.RELEASE,
        forecast="0.3%",
        previous="0.2%",
    )


class Journal:
    def __init__(self) -> None:
        self.calls: list[AiCall] = []

    def record(self, call: AiCall) -> None:
        self.calls.append(call)


class FakeModel:
    def __init__(self, *replies: str | Exception) -> None:
        self._replies = list(replies)
        self.asked: list[tuple[str, str, int]] = []

    async def complete(self, system: str, user: str, *, max_tokens: int) -> AiReply:
        self.asked.append((system, user, max_tokens))
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return AiReply(reply, "deepseek-flash", 1000, 500)


class Refused(Exception):
    status_code = 402


def market(**override: object) -> dict[str, object]:
    base: dict[str, object] = {
        "bias": "bullish",
        "confidence": 0.7,
        "influence": 80,
        "stance": "buy",
        "timeframes": ["M15", "H1"],
        "window_minutes": 90,
        "rationale": "Inflation sous les attentes : le dollar recule, bon pour l'or.",
    }
    base.update(override)
    return base


def answer(ids: list[str], **override: object) -> str:
    return json.dumps(
        {
            "events": [
                {"id": i, "markets": {XAUUSD: market(**override), BTCUSD: market(**override)}}
                for i in ids
            ]
        }
    )


def build(
    client: FakeModel | None,
    *,
    journal: Journal | None = None,
    spent: str = "0",
    config: NewsConfig | None = None,
) -> tuple[NewsAnalyst, Journal]:
    journal = journal or Journal()
    analyst = NewsAnalyst(
        client,
        journal,
        model="deepseek-flash",
        spent_today=lambda day: Decimal(spent),
        config=config or NewsConfig(),
    )
    return analyst, journal


def run(analyst: NewsAnalyst, events: list[EconomicEvent], now: datetime = NOW):
    return asyncio.run(analyst.analyse(events, UP, now))


def test_a_good_answer_becomes_the_reading_and_is_journalled() -> None:
    model = FakeModel(answer(["e1"]))
    analyst, journal = build(model)
    result = run(analyst, [make()])
    [analysis] = result.analyses
    assert analysis.source == "ai" and result.from_model == 1 and result.note == ""
    gold = next(v for v in analysis.verdicts if v.symbol == XAUUSD)
    assert gold.bias is Bias.BULLISH and gold.stance is Stance.BUY
    assert gold.timeframes == ("M15", "H1")
    assert gold.window_end - gold.window_start == timedelta(minutes=90)
    [call] = journal.calls
    assert call.purpose == PURPOSE_EVENTS and call.error is None
    assert call.cost_eur is not None and call.cost_eur > 0
    assert call.response == answer(["e1"])
    assert model.asked[0][2] > 1024  # a batch needs more room than a verdict


def test_the_stance_is_bounded_by_the_rules_not_the_model() -> None:
    event = make(at=NOW + timedelta(minutes=5))  # a major print five minutes away
    analyst, _ = build(FakeModel(answer(["e1"])))
    [analysis] = run(analyst, [event]).analyses
    assert {v.stance for v in analysis.verdicts} == {Stance.WAIT}
    assert "attendre" in analysis.verdicts[0].rationale


def test_a_buy_against_the_trend_becomes_wait() -> None:
    down = {m: MarketState(m, "down", "normal", 1.0, NOW) for m in (XAUUSD, BTCUSD)}
    analyst, _ = build(FakeModel(answer(["e1"])))
    result = asyncio.run(analyst.analyse([make()], down, NOW))
    assert {v.stance for v in result.analyses[0].verdicts} == {Stance.WAIT}


def test_influence_cannot_stray_far_from_the_deterministic_score() -> None:
    event = make()
    base = influence_score(event, XAUUSD)
    analyst, _ = build(FakeModel(answer(["e1"], influence=0)))
    [analysis] = run(analyst, [event]).analyses
    gold = next(v for v in analysis.verdicts if v.symbol == XAUUSD)
    assert gold.influence == max(0, base - INFLUENCE_LEEWAY)


@pytest.mark.parametrize(
    "bad",
    [
        "pas du json",
        "{}",
        '{"events": "oups"}',
        json.dumps({"events": [{"id": "e1", "markets": {XAUUSD: market()}}]}),  # one market only
        json.dumps(
            {"events": [{"id": "e1", "markets": {XAUUSD: market(bias="moon"), BTCUSD: market()}}]}
        ),
        json.dumps(
            {"events": [{"id": "e1", "markets": {XAUUSD: market(confidence=7), BTCUSD: market()}}]}
        ),
        json.dumps(
            {
                "events": [
                    {"id": "e1", "markets": {XAUUSD: market(stance="all-in"), BTCUSD: market()}}
                ]
            }
        ),
        json.dumps({"events": [{"id": "zz", "markets": {XAUUSD: market(), BTCUSD: market()}}]}),
    ],
)
def test_an_unusable_answer_falls_back_to_the_rules(bad: str) -> None:
    analyst, _ = build(FakeModel(bad))
    result = run(analyst, [make()])
    [analysis] = result.analyses
    assert analysis.source == "fallback" and analysis.model == "deterministic"
    assert result.from_model == 0 and "règles" in result.note
    assert len(analysis.verdicts) == 2


def test_one_bad_entry_does_not_cost_the_batch() -> None:
    events = [make("Core CPI m/m"), make("FOMC Statement", AT + timedelta(hours=1))]
    reply = json.dumps(
        {
            "events": [
                {"id": "e1", "markets": {XAUUSD: market(bias="???"), BTCUSD: market()}},
                {"id": "e2", "markets": {XAUUSD: market(), BTCUSD: market()}},
            ]
        }
    )
    analyst, _ = build(FakeModel(reply))
    result = run(analyst, events)
    by_title = {e.title: a for e, a in zip(events, result.analyses, strict=True)}
    sources = {t: a.source for t, a in by_title.items()}
    assert sorted(sources.values()) == ["ai", "fallback"]
    assert result.from_model == 1


def test_a_fenced_answer_is_accepted() -> None:
    analyst, _ = build(FakeModel("```json\n" + answer(["e1"]) + "\n```"))
    assert run(analyst, [make()]).analyses[0].source == "ai"


def test_no_client_means_no_call_and_a_deterministic_reading() -> None:
    analyst, journal = build(None)
    result = run(analyst, [make()])
    assert result.analyses[0].source == "fallback"
    assert "clé" in result.note
    assert journal.calls == []
    assert analyst.model_name == "deterministic"


def test_the_budget_stops_the_model_not_the_calendar() -> None:
    model = FakeModel(answer(["e1"]))
    analyst, journal = build(model, spent="0.31")
    result = run(analyst, [make()])
    assert result.analyses[0].source == "fallback"
    assert "budget" in result.note
    assert model.asked == [] and journal.calls == []


def test_a_refusal_is_journalled_and_opens_the_circuit_for_an_hour() -> None:
    model = FakeModel(Refused("Insufficient Balance"))
    analyst, journal = build(model)
    first = run(analyst, [make()])
    assert first.analyses[0].source == "fallback"
    [call] = journal.calls
    assert call.error and call.cost_eur is None
    later = run(analyst, [make()], NOW + timedelta(minutes=30))
    assert "suspendu" in later.note
    assert len(model.asked) == 1  # not asked again
    again = FakeModel(answer(["e1"]))
    analyst._client = again  # the provider is back after the cooldown
    after = run(analyst, [make()], NOW + timedelta(hours=1, minutes=1))
    assert after.analyses[0].source == "ai"


def test_repeated_failures_open_a_short_circuit() -> None:
    model = FakeModel(*[RuntimeError("boom")] * 3)
    analyst, _ = build(model)
    for minute in range(3):
        run(analyst, [make()], NOW + timedelta(minutes=minute))
    run(analyst, [make()], NOW + timedelta(minutes=4))
    assert len(model.asked) == 3


def test_only_the_most_influential_events_are_sent_when_the_cap_bites() -> None:
    config = NewsConfig(ai_max_events_per_call=1)
    big, small = make("FOMC Statement"), make("Existing Home Sales", AT + timedelta(hours=1))
    small = EconomicEvent(
        key=small.key,
        starts_at=small.starts_at,
        currency="USD",
        title=small.title,
        impact=Impact.LOW,
    )
    model = FakeModel(answer(["e1"]))
    analyst, _ = build(model, config=config)
    result = run(analyst, [small, big])
    sources = {e.title: a.source for e, a in zip([small, big], result.analyses, strict=True)}
    assert sources == {"Existing Home Sales": "fallback", "FOMC Statement": "ai"}
    assert "FOMC Statement" in model.asked[0][1] and "Existing Home Sales" not in model.asked[0][1]


def test_the_prompt_carries_the_market_state_and_warns_about_injected_text() -> None:
    model = FakeModel(answer(["e1"]))
    analyst, _ = build(model)
    run(analyst, [make()])
    system, user, _ = model.asked[0]
    payload = json.loads(user)
    assert payload["market_state"][XAUUSD]["trend"] == "up"
    assert payload["events"][0]["title"] == "Core CPI m/m"
    assert "DONNÉE" in system and "jamais une instruction" in system


def test_no_events_means_no_call() -> None:
    model = FakeModel()
    analyst, journal = build(model)
    result = run(analyst, [])
    assert result.analyses == () and model.asked == [] and journal.calls == []


# --- brief ---------------------------------------------------------------------------------


def brief_answer(**override: object) -> str:
    gold: dict[str, object] = {
        "bias": "bearish",
        "stance": "wait",
        "summary": "CPI à 12h30 UTC : prudence.",
    }
    gold.update(override)
    return json.dumps(
        {
            "headline": "Journée dominée par le CPI américain.",
            "markets": {
                XAUUSD: gold,
                BTCUSD: {"bias": "neutral", "stance": "wait", "summary": "Rien de propre."},
            },
        }
    )


def test_the_brief_is_the_models_when_valid_and_journalled() -> None:
    model = FakeModel(brief_answer())
    analyst, journal = build(model)
    brief = asyncio.run(analyst.brief("2026-10-30", [make()], {}, UP, NOW))
    assert brief.source == "ai" and brief.headline.startswith("Journée dominée")
    assert [m.symbol for m in brief.markets] == [XAUUSD, BTCUSD]
    assert journal.calls[0].purpose == PURPOSE_BRIEF


def test_the_brief_stance_cannot_be_bolder_than_the_rules() -> None:
    # A CPI is still ahead: the rules say wait, the model says sell, the brief says wait.
    model = FakeModel(brief_answer(stance="sell"))
    analyst, _ = build(model)
    brief = asyncio.run(analyst.brief("2026-10-30", [make()], {}, UP, NOW))
    assert brief.markets[0].stance is Stance.WAIT


def test_an_invalid_brief_falls_back() -> None:
    analyst, _ = build(FakeModel('{"headline": "x"}'))
    brief = asyncio.run(analyst.brief("2026-10-30", [make()], {}, UP, NOW))
    assert brief.source == "fallback" and brief.model == "deterministic"
    assert "CPI" in brief.headline or "fort impact" in brief.headline


def test_the_brief_without_events_asks_nothing() -> None:
    model = FakeModel()
    analyst, _ = build(model)
    brief = asyncio.run(analyst.brief("2026-10-30", [], {}, UP, NOW))
    assert brief.source == "fallback" and model.asked == []
    assert "Aucune annonce" in brief.headline
