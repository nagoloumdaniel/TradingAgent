from dataclasses import replace
from decimal import Decimal as D

import pytest
from tests.risk.builders import BTC_LIKE, EQUITY, GOLD_BUY, GOLD_QUOTE, LOGIN, RISK, context

from tradingagent.core.mode import TradingMode
from tradingagent.core.states import RiskOutcome
from tradingagent.data.market_calendar import SlotStatus
from tradingagent.risk.engine import AccountModeMismatchError, decide
from tradingagent.risk.model import limits_for


def test_a_sound_trade_is_authorized_with_its_size() -> None:
    decision = decide(context(), LOGIN)
    assert decision.outcome is RiskOutcome.AUTHORIZED
    assert decision.sizing is not None
    assert decision.sizing.volume == D("0.02")
    assert decision.refusals == ()


def test_a_size_bounded_by_margin_is_reduced() -> None:
    decision = decide(BTC_LIKE, LOGIN)
    assert decision.outcome is RiskOutcome.REDUCED
    assert decision.sizing is not None
    assert decision.sizing.volume == D("0.07")
    assert "margin" in decision.reason


def test_a_refusal_lists_every_limit_hit() -> None:
    ctx = context(market=SlotStatus.CLOSED)
    ctx = replace(ctx, portfolio=replace(ctx.portfolio, trades_today=9))
    decision = decide(ctx, LOGIN)
    assert decision.outcome is RiskOutcome.REFUSED
    assert {check.name for check in decision.refusals} == {"trading_hours", "trades_today"}
    assert "trading_hours" in decision.reason
    assert "trades_today" in decision.reason
    assert decision.sizing is None


def test_a_sizing_error_refuses_never_falls_back() -> None:
    decision = decide(context(quote=replace(GOLD_QUOTE, loss_one_lot=None)), LOGIN)
    assert decision.outcome is RiskOutcome.REFUSED
    assert [check.name for check in decision.refusals] == ["sizing"]
    assert decision.sizing is None


def test_without_a_stop_sizing_is_not_attempted() -> None:
    decision = decide(context(intent=replace(GOLD_BUY, stop_loss=None)), LOGIN)
    assert "sizing" in {check.name for check in decision.refusals}
    assert "stop_loss" in {check.name for check in decision.refusals}


def test_the_checks_record_keeps_every_verdict() -> None:
    record = decide(context(), LOGIN).checks_record()
    assert len(record) == 16
    assert all(entry["passed"] for entry in record.values())


@pytest.mark.parametrize(
    ("mode", "is_demo", "login"),
    [
        (TradingMode.DEMO, False, LOGIN),
        (TradingMode.PAPER, False, LOGIN),
        (TradingMode.LIVE, True, LOGIN),
        (TradingMode.DEMO, True, 99999999),
    ],
)
def test_an_account_contradicting_the_mode_stops_everything(
    mode: TradingMode, is_demo: bool, login: int
) -> None:
    ctx = context(mode)
    ctx = replace(ctx, account=replace(ctx.account, is_demo=is_demo, login=login))
    with pytest.raises(AccountModeMismatchError):
        decide(ctx, LOGIN)


def test_thresholds_differ_between_demo_and_live() -> None:
    demo, live = limits_for(TradingMode.DEMO, RISK), limits_for(TradingMode.LIVE, RISK)
    assert (demo.risk_per_trade, live.risk_per_trade) == (D("0.005"), D("0.02"))
    assert (demo.daily_loss, live.daily_loss) == (D("0.02"), D("0.05"))
    assert (demo.weekly_loss, live.weekly_loss) == (D("0.06"), D("0.1"))
    assert (demo.max_drawdown, live.max_drawdown) == (D("0.1"), D("0.2"))


def test_switching_the_mode_changes_the_ceiling_applied() -> None:
    """Same 100 EUR account, 3 EUR lost today: over 2 % in demo, under 5 % in live."""

    def daily_verdict(mode: TradingMode) -> bool:
        ctx = context(mode)
        ctx = replace(
            ctx,
            account=replace(ctx.account, equity=D(100), free_margin=D(100)),
            portfolio=replace(
                ctx.portfolio,
                day_start_equity=D(100),
                week_start_equity=D(100),
                equity_peak=D(100),
                day_pnl=D(-3),
            ),
        )
        return next(c for c in decide(ctx, LOGIN).checks if c.name == "daily_loss").passed

    assert daily_verdict(TradingMode.DEMO) is False
    assert daily_verdict(TradingMode.LIVE) is True


def test_an_rm019_ineligible_instrument_is_refused_live_and_accepted_in_demo() -> None:
    live = context(TradingMode.LIVE)
    live = replace(live, account=replace(live.account, equity=D(100), free_margin=D(100)))
    demo = context(TradingMode.DEMO)
    live_decision = decide(live, LOGIN)
    assert live_decision.outcome is RiskOutcome.REFUSED
    assert "live_eligibility" in {check.name for check in live_decision.refusals}
    assert decide(demo, LOGIN).outcome is RiskOutcome.AUTHORIZED


def test_live_sizing_never_exceeds_the_declared_capital() -> None:
    # A rich real account still sizes on 100 EUR: 2 EUR at risk, below the minimum lot.
    live = context(TradingMode.LIVE)
    assert live.account.equity == EQUITY
    decision = decide(live, LOGIN)
    sizing_check = next(c for c in decision.checks if c.name == "sizing")
    assert not sizing_check.passed
    assert "minimum lot" in sizing_check.reason
