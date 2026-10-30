"""The portfolio snapshot carries the plain streak and the lock of today (UTC)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Engine
from tests.reporting.test_campaign import _chain

from tradingagent.risk.model import AccountState, PortfolioState
from tradingagent.risk.streak import DayLossRun
from tradingagent.runtime.portfolio import PortfolioBuilder

NOW = datetime(2026, 10, 30, 15, 0, tzinfo=UTC)
ACCOUNT = AccountState(1, True, "EUR", Decimal(1000), Decimal(1000))


def closed(engine: Engine, trades: list[tuple[str, float]]) -> None:
    """Trades as (pnl, hours before NOW), oldest first."""
    for index, (pnl, hours_ago) in enumerate(trades):
        _chain(
            engine,
            index=index,
            market="XAUUSD",
            ref="vwap_pullback@1.0.0",
            closed_at=NOW - timedelta(hours=hours_ago),
            pnl=Decimal(pnl),
        )


def build(engine: Engine, at: datetime = NOW) -> PortfolioState:
    return PortfolioBuilder(engine).build(account=ACCOUNT, open_positions=(), trades_today=0, at=at)


def test_three_losses_today_are_counted_today(engine: Engine) -> None:
    closed(engine, [("-1", 3), ("-1", 2), ("-1", 1)])
    state = build(engine)
    assert state.consecutive_losses == 3
    assert state.losses_today == DayLossRun(3, NOW.date())
    assert state.last_loss_at == NOW - timedelta(hours=1)


def test_losses_closed_yesterday_are_not_counted_today(engine: Engine) -> None:
    # 22:00 and 23:00 yesterday, then one loss at 14:00 today.
    closed(engine, [("-1", 17), ("-1", 16), ("-1", 1)])
    state = build(engine)
    assert state.consecutive_losses == 3  # the hour-based cooldown keeps its own view
    assert state.losses_today.longest == 1


def test_the_streak_of_the_day_is_gone_the_next_day(engine: Engine) -> None:
    closed(engine, [("-1", 3), ("-1", 2), ("-1", 1)])
    next_day = NOW + timedelta(hours=10)
    assert build(engine, next_day).losses_today == DayLossRun(0, next_day.date())


def test_three_losses_then_a_late_win_keep_the_day_locked(engine: Engine) -> None:
    # The third loss closes at 12:00, a position opened earlier closes in profit at 14:00.
    closed(engine, [("-1", 5), ("-1", 4), ("-1", 3), ("8", 1)])
    state = build(engine)
    assert state.consecutive_losses == 0  # the hour-based cooldown sees a win
    assert state.losses_today.longest == 3


def test_two_losses_a_win_and_a_loss_do_not_lock_the_day(engine: Engine) -> None:
    closed(engine, [("-1", 5), ("-1", 4), ("2", 3), ("-1", 1)])
    assert build(engine).losses_today.longest == 2


def test_the_lock_sees_every_trade_of_the_day_beyond_the_streak_lookback(engine: Engine) -> None:
    # 150 wins after the losses must not push the early run out of view.
    closed(engine, [("-1", 14.5), ("-1", 14.4), ("-1", 14.3)])
    closed_more = [("1", 14 - index * 0.05) for index in range(0, 210)]
    for index, (pnl, hours_ago) in enumerate(closed_more, start=10):
        _chain(
            engine,
            index=index,
            market="XAUUSD",
            ref="vwap_pullback@1.0.0",
            closed_at=NOW - timedelta(hours=hours_ago),
            pnl=Decimal(pnl),
        )
    assert build(engine).losses_today.longest == 3
