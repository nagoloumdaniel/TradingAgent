"""Each control has a passing case (the base context) and at least one refusal."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from typing import Any

import pytest
from tests.risk.builders import EQUITY, GOLD, GOLD_BUY, GOLD_QUOTE, NOW, context

from tradingagent.core.halt import HaltStatus
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.data.market_calendar import SlotStatus
from tradingagent.risk.checks import CHECKS, RiskContext
from tradingagent.risk.model import OpenPosition


def run(name: str, ctx: RiskContext):
    [check] = [c for c in CHECKS if c.__name__ == f"check_{name}"]
    return check(ctx)


def portfolio(**changes: Any) -> RiskContext:
    base = context()
    return replace(base, portfolio=replace(base.portfolio, **changes))


ALL = [c.__name__.removeprefix("check_") for c in CHECKS]


@pytest.mark.parametrize("name", ALL)
def test_every_check_passes_on_a_sound_trade(name: str) -> None:
    result = run(name, context())
    assert result.passed, result.reason
    assert result.name == name


REFUSALS = {
    "not_halted/operator_halt": context(halt=HaltStatus(True, False, ("global: stop (server)",))),
    "stop_loss/missing": context(intent=replace(GOLD_BUY, stop_loss=None)),
    "stop_loss/wrong_side": context(intent=replace(GOLD_BUY, stop_loss=D(2410))),
    "stop_loss/between_bid_and_ask": context(intent=replace(GOLD_BUY, stop_loss=D("2400.1"))),
    "stop_loss/too_close_to_bid": context(intent=replace(GOLD_BUY, stop_loss=D("2399.95"))),
    "stop_loss/sell_wrong_side": context(
        intent=replace(GOLD_BUY, direction=Direction.SELL, stop_loss=D(2390))
    ),
    "entry_zone/above": context(quote=replace(GOLD_QUOTE, bid=D(2402), ask=D("2402.2"))),
    "entry_zone/below": context(quote=replace(GOLD_QUOTE, bid=D(2390), ask=D("2390.2"))),
    "daily_loss/at_limit": portfolio(day_pnl=-EQUITY * D("0.02")),
    "weekly_loss/at_limit": portfolio(week_pnl=-EQUITY * D("0.06")),
    "drawdown/beyond_limit": portfolio(equity_peak=D(6200)),  # 702 EUR below a 6,200 peak
    "open_positions/full": portfolio(
        open_positions=(OpenPosition("BTCUSD", D("0.01")), OpenPosition("EURUSD", D("0.01")))
    ),
    "market_positions/already_in": portfolio(open_positions=(OpenPosition("XAUUSD", D("0.01")),)),
    "trades_today/limit": portfolio(trades_today=4),
    "spread/wide": context(quote=replace(GOLD_QUOTE, bid=D("2398.7"), ask=D("2400.2"))),
    "spread/no_stop": context(intent=replace(GOLD_BUY, stop_loss=None)),
    "margin/insufficient": context(account=replace(context().account, free_margin=D(300))),
    "margin/unknown": context(quote=replace(GOLD_QUOTE, margin_one_lot=None)),
    "account_currency/usd": context(account=replace(context().account, currency="USD")),
    "trading_hours/closed": context(market=SlotStatus.CLOSED),
    "trading_hours/uncertain": context(market=SlotStatus.UNCERTAIN),
    "cooldown/paused": portfolio(consecutive_losses=3, last_loss_at=NOW - timedelta(hours=3)),
    "live_eligibility/gold_at_100_eur": context(
        TradingMode.LIVE, account=replace(context(TradingMode.LIVE).account, equity=D(100))
    ),
}


@pytest.mark.parametrize("case", REFUSALS)
def test_each_check_refuses_when_its_limit_is_hit(case: str) -> None:
    name = case.split("/")[0]
    result = run(name, REFUSALS[case])
    assert not result.passed, result.reason
    assert result.reason


def test_every_check_has_a_refusal_case() -> None:
    assert {case.split("/")[0] for case in REFUSALS} == set(ALL)


def test_a_sell_with_a_stop_above_is_accepted() -> None:
    sell = replace(GOLD_BUY, direction=Direction.SELL, stop_loss=D("2412.3073"))
    assert run("stop_loss", context(intent=sell)).passed


def test_a_loss_leaving_room_for_one_more_full_risk_passes() -> None:
    room = EQUITY * D("0.02") - EQUITY * D("0.005")  # limit minus this trade's budget
    assert run("daily_loss", portfolio(day_pnl=-room)).passed
    assert not run("daily_loss", portfolio(day_pnl=-room - D("0.01"))).passed


def test_a_trade_whose_stop_would_breach_the_daily_limit_is_refused() -> None:
    # Live, 100 EUR: 4.90 EUR lost, 2 EUR at stake would end the day at -6.90, over 5.
    live = context(TradingMode.LIVE)
    ctx = replace(
        live,
        account=replace(live.account, equity=D(100)),
        portfolio=replace(live.portfolio, day_start_equity=D(100), day_pnl=D("-4.90")),
    )
    assert not run("daily_loss", ctx).passed


def test_a_losing_streak_without_its_time_pauses_by_default() -> None:
    assert not run("cooldown", portfolio(consecutive_losses=3, last_loss_at=None)).passed


def test_a_stop_valid_from_the_ask_but_too_close_to_the_bid_is_refused() -> None:
    # Minimum 0.10: 0.25 from the ask, but MT5 measures 0.05 from the bid.
    assert (
        "broker minimum"
        in run("stop_loss", context(intent=replace(GOLD_BUY, stop_loss=D("2399.95")))).reason
    )


def test_gains_never_trip_a_loss_limit() -> None:
    assert run("daily_loss", portfolio(day_pnl=D(500))).passed


def test_cooldown_lifts_after_its_duration() -> None:
    ctx = portfolio(consecutive_losses=3, last_loss_at=NOW - timedelta(hours=4))
    assert run("cooldown", ctx).passed


def test_a_shorter_streak_does_not_pause() -> None:
    assert run("cooldown", portfolio(consecutive_losses=2, last_loss_at=NOW)).passed


def test_live_loss_limits_use_the_declared_capital_not_the_equity() -> None:
    # 6 EUR lost on a 5,497 EUR real account: 0.1 % of equity, but over 5 % of 100 EUR.
    live = context(TradingMode.LIVE)
    ctx = replace(live, portfolio=replace(live.portfolio, day_pnl=D(-6)))
    assert not run("daily_loss", ctx).passed


def test_eligibility_reason_names_the_capital_that_would_lift_it() -> None:
    live = context(TradingMode.LIVE)
    ctx = replace(live, account=replace(live.account, equity=D(100)))
    assert "547.00 EUR of capital" in run("live_eligibility", ctx).reason


def test_gold_is_live_eligible_with_a_tight_enough_stop() -> None:
    # 1.5 USD stop: the minimum lot risks 0.01 x 133.3 = 1.33 EUR, under 2 EUR.
    live = context(TradingMode.LIVE)
    loss = GOLD.contract_size * D("1.5") * D("0.888786")
    ctx = replace(
        live,
        account=replace(live.account, equity=D(100)),
        intent=replace(GOLD_BUY, stop_loss=D("2398.7")),
        quote=replace(GOLD_QUOTE, loss_one_lot=loss),
    )
    assert run("live_eligibility", ctx).passed


def test_startup_eligibility_without_a_typical_stop_is_unknown_and_refused() -> None:
    from tradingagent.risk.eligibility import Eligibility, live_eligibility

    live = context(TradingMode.LIVE)
    verdict = live_eligibility(GOLD, GOLD_QUOTE, None, live.limits, D(100))
    assert verdict.status is Eligibility.UNKNOWN


def test_a_stop_at_the_entry_price_is_refused_even_without_broker_minimum() -> None:
    ctx = context(
        spec=replace(GOLD, stops_level=0), intent=replace(GOLD_BUY, stop_loss=D("2400.2"))
    )
    assert not run("stop_loss", ctx).passed


def test_live_eligibility_without_a_loss_per_lot_is_unknown_and_refused() -> None:
    live = context(TradingMode.LIVE, quote=replace(GOLD_QUOTE, loss_one_lot=None))
    assert not run("live_eligibility", live).passed


def test_a_halt_refusal_names_who_stopped_and_why() -> None:
    halt = HaltStatus(True, False, ("global: weekly loss reached (automatic, agent)",))
    assert "weekly loss reached" in run("not_halted", context(halt=halt)).reason
