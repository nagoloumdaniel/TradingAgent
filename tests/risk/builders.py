from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from typing import Any

from tradingagent.config.agent import LiveRiskProfile, RiskConfig, RiskProfile
from tradingagent.core.halt import TRADING
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.data.market_calendar import SlotStatus
from tradingagent.risk.checks import RiskContext
from tradingagent.risk.model import (
    AccountState,
    InstrumentSpec,
    MarketQuote,
    PortfolioState,
    TradeIntent,
    limits_for,
)

LOGIN = 40123456
NOW = datetime(2026, 10, 6, 12, 0, 5, tzinfo=UTC)
USD = D("0.888786")
EQUITY = D("5497.74")

RISK = RiskConfig(
    simulated=RiskProfile(
        risk_per_trade_pct=D("0.5"),
        daily_loss_pct=D(2),
        weekly_loss_pct=D(6),
        max_drawdown_pct=D(10),
        max_open_positions=2,
        max_positions_per_market=1,
    ),
    live=LiveRiskProfile(
        risk_per_trade_pct=D(2),
        daily_loss_pct=D(5),
        weekly_loss_pct=D(10),
        max_drawdown_pct=D(20),
        max_open_positions=2,
        max_positions_per_market=1,
        reference_capital=D(100),
        currency="EUR",
    ),
)

GOLD = InstrumentSpec("XAUUSD", D(100), D("0.01"), D("0.01"), D(100), D("0.01"), 10)
# Ask 2400.2, stop 12.3073 below: the specification's example 1.
GOLD_QUOTE = MarketQuote(D(2400), D("2400.2"), D("1094.00"), D(18393), USD)
GOLD_BUY = TradeIntent(1, "XAUUSD", Direction.BUY, D(2399), D(2401), D("2387.8927"))


def context(mode: TradingMode = TradingMode.DEMO, **changes: Any) -> RiskContext:
    """A gold demo trade that passes every check; override one field to break one."""
    base = RiskContext(
        intent=GOLD_BUY,
        account=AccountState(LOGIN, mode is not TradingMode.LIVE, "EUR", EQUITY, EQUITY),
        spec=GOLD,
        quote=GOLD_QUOTE,
        portfolio=PortfolioState(
            open_positions=(),
            trades_today=0,
            day_start_equity=EQUITY,
            day_pnl=D(0),
            week_start_equity=EQUITY,
            week_pnl=D(0),
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


# The specification's example 3: bitcoin in demo, bounded by margin at 1:2 leverage.
BTC_LIKE = context(
    spec=InstrumentSpec("BTCUSD", D(1), D("0.01"), D("0.01"), D(100), D("0.01"), 10),
    quote=MarketQuote(D(84000), D(84010), D("92.00"), D(37634), USD),
    intent=TradeIntent(2, "BTCUSD", Direction.BUY, D(83990), D(84020), D("83905.8738")),
)
