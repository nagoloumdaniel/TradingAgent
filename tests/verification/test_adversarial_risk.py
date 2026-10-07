"""Adversarial checks on the risk gate: no signal may bypass it (F-011, RM-004..RM-019).

The verifier rebuilds its own snapshot instead of reusing a teammate's builder, so a
defect hidden in a shared fixture cannot be hidden from this suite too.
"""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from tradingagent.config.agent import LiveRiskProfile, RiskConfig, RiskProfile
from tradingagent.core.account import AccountModeMismatchError
from tradingagent.core.halt import TRADING, HaltStatus
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import RiskOutcome
from tradingagent.data.market_calendar import SlotStatus
from tradingagent.risk.checks import RiskContext
from tradingagent.risk.engine import decide
from tradingagent.risk.model import (
    AccountState,
    InstrumentSpec,
    MarketQuote,
    OpenPosition,
    PortfolioState,
    TradeIntent,
    limits_for,
)

NOW = datetime(2026, 10, 7, 12, 0, 5, tzinfo=UTC)
LOGIN = 40123456
EQUITY = Decimal("5497.74")

RISK = RiskConfig(
    simulated=RiskProfile(
        risk_per_trade_pct=Decimal("0.5"),
        daily_loss_pct=Decimal(2),
        weekly_loss_pct=Decimal(6),
        max_drawdown_pct=Decimal(10),
        max_open_positions=2,
        max_positions_per_market=1,
    ),
    live=LiveRiskProfile(
        risk_per_trade_pct=Decimal(2),
        daily_loss_pct=Decimal(5),
        weekly_loss_pct=Decimal(10),
        max_drawdown_pct=Decimal(20),
        max_open_positions=2,
        max_positions_per_market=1,
        reference_capital=Decimal(100),
        currency="EUR",
    ),
)

GOLD = InstrumentSpec(
    "XAUUSD", Decimal(100), Decimal("0.01"), Decimal("0.01"), Decimal(100), Decimal("0.01"), 10
)
GOLD_QUOTE = MarketQuote(
    Decimal(2400), Decimal("2400.2"), Decimal("1094.00"), Decimal(18393), Decimal("0.888786")
)
GOLD_BUY = TradeIntent(
    1, "XAUUSD", Direction.BUY, Decimal(2399), Decimal(2401), Decimal("2387.8927")
)


def context(mode: TradingMode = TradingMode.DEMO, **changes: Any) -> RiskContext:
    """A gold demo trade that passes every control; override one field to break one."""
    base = RiskContext(
        intent=GOLD_BUY,
        account=AccountState(LOGIN, True, "EUR", EQUITY, EQUITY),
        spec=GOLD,
        quote=GOLD_QUOTE,
        portfolio=PortfolioState(
            open_positions=(),
            trades_today=0,
            day_start_equity=EQUITY,
            day_pnl=Decimal(0),
            week_start_equity=EQUITY,
            week_pnl=Decimal(0),
            equity_peak=EQUITY,
            consecutive_losses=0,
            last_loss_at=None,
        ),
        market=SlotStatus.OPEN,
        limits=limits_for(mode, RISK),
        now=NOW,
        halt=TRADING,
    )
    return replace(base, **changes)


def refusal_names(ctx: RiskContext) -> set[str]:
    decision = decide(ctx, LOGIN)
    assert decision.outcome is RiskOutcome.REFUSED
    return {check.name for check in decision.refusals}


# --- the baseline is meaningful ---------------------------------------------------


def test_the_reference_trade_is_authorized() -> None:
    decision = decide(context(), LOGIN)
    assert decision.outcome is RiskOutcome.AUTHORIZED
    assert decision.sizing is not None
    assert decision.sizing.volume == Decimal("0.02")


# --- one control at a time --------------------------------------------------------


def test_a_missing_stop_is_refused() -> None:
    assert "stop_loss" in refusal_names(context(intent=replace(GOLD_BUY, stop_loss=None)))


def test_a_stop_on_the_wrong_side_is_refused() -> None:
    # A buy with its stop above the trigger is not protective: it would guarantee a loss.
    broken = replace(GOLD_BUY, stop_loss=Decimal("2500"))
    assert "stop_loss" in refusal_names(context(intent=broken))


def test_a_volume_below_the_minimum_lot_is_refused() -> None:
    tiny_capital = AccountState(LOGIN, True, "EUR", Decimal("1.00"), Decimal("1000000"))
    names = refusal_names(context(account=tiny_capital))
    assert "sizing" in names, names


def test_an_active_halt_refuses_every_order() -> None:
    halt = HaltStatus(halted=True, reasons=("global: emergency stop",))
    assert "not_halted" in refusal_names(context(halt=halt))


def test_the_open_positions_cap_cannot_be_exceeded() -> None:
    portfolio = replace(
        context().portfolio,
        open_positions=(
            OpenPosition("XAUUSD", Decimal("0.01")),
            OpenPosition("BTCUSD", Decimal("0.01")),
        ),
    )
    names = refusal_names(context(portfolio=portfolio))
    assert {"open_positions", "market_positions"} <= names, names


def test_a_wider_spread_than_allowed_is_refused() -> None:
    quote = replace(GOLD_QUOTE, bid=Decimal(2390), ask=Decimal(2400))
    names = refusal_names(context(quote=quote))
    assert "spread" in names, names


def test_a_market_that_is_not_provably_open_is_refused() -> None:
    assert "trading_hours" in refusal_names(context(market=SlotStatus.UNCERTAIN))
    assert "trading_hours" in refusal_names(context(market=SlotStatus.CLOSED))


def test_a_non_eur_account_is_refused() -> None:
    account = replace(context().account, currency="USD")
    assert "account_currency" in refusal_names(context(account=account))


def test_the_cooldown_after_a_losing_streak_is_enforced() -> None:
    portfolio = replace(
        context().portfolio,
        consecutive_losses=3,
        last_loss_at=NOW,
    )
    assert "cooldown" in refusal_names(context(portfolio=portfolio))


def test_one_position_per_market_is_enforced() -> None:
    portfolio = replace(
        context().portfolio, open_positions=(OpenPosition("XAUUSD", Decimal("0.01")),)
    )
    assert "market_positions" in refusal_names(context(portfolio=portfolio))


# --- RM-017 is fatal, never a refusal ---------------------------------------------


def test_an_account_mismatch_raises_instead_of_refusing() -> None:
    with pytest.raises(AccountModeMismatchError):
        decide(context(), LOGIN + 1)


def test_a_real_account_in_demo_mode_raises() -> None:
    account = replace(context().account, is_demo=False)
    with pytest.raises(AccountModeMismatchError):
        decide(context(account=account), LOGIN)


def test_a_demo_account_in_live_mode_raises() -> None:
    with pytest.raises(AccountModeMismatchError):
        decide(context(TradingMode.LIVE), LOGIN)


# --- LIVE adds its own ceiling (RM-019) -------------------------------------------


def test_live_mode_refuses_the_minimum_lot_that_the_micro_capital_cannot_carry() -> None:
    # reference_capital is 100 EUR: the minimum lot alone risks more than RM-005 allows.
    real_account = replace(context().account, is_demo=False)
    names = refusal_names(context(TradingMode.LIVE, account=real_account))
    assert "live_eligibility" in names, names
