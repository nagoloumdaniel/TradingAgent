"""The day's result, as the operator reads it on three different surfaces.

The figure has to be the right one — it is the number the operator acts on — so the tests
here are about the arithmetic and about the two cases where a number would be a lie: a
balance that no snapshot supports, and modes that must never be added together.
"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Engine, insert, select

from tradingagent.analytics.daily import (
    DailyResult,
    closed_trades,
    daily_result,
    day_bounds,
)
from tradingagent.analytics.model import Performance
from tradingagent.analytics.performance import compute_performance
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import OrderState, PositionState, SignalState
from tradingagent.storage.account import AccountStore
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import (
    OrderRow,
    PositionRow,
    SignalRow,
    StrategyVersionRow,
    TradeRow,
)

DAY = datetime(2026, 10, 7, tzinfo=UTC)


@pytest.fixture
def db_engine(tmp_path) -> Iterator[Engine]:
    """A migrated SQLite database: the query reads tables, so it needs the real schema."""
    url = f"sqlite:///{tmp_path / 'daily.db'}"
    upgrade(url)
    engine = create_database_engine(url)
    yield engine
    engine.dispose()


def _performance(net: str = "12.50", trades: int = 3) -> Performance:
    """A real `Performance`, adjusted: enumerating its 24 fields would rot on the next change."""
    return replace(
        compute_performance([]),
        trades=trades,
        wins=2,
        losses=1,
        win_rate=Decimal("0.6667"),
        gross_profit=Decimal("30.00"),
        gross_loss=Decimal("-17.50"),
        net_profit=Decimal(net),
        profit_factor=1.71,
        expectancy=4.17,
        average_win=15.0,
        average_loss=-17.5,
        best=Decimal("20.00"),
        worst=Decimal("-17.50"),
        max_drawdown=Decimal("17.50"),
    )


# --- the window -------------------------------------------------------------------------


def test_the_window_is_half_open_so_a_trade_belongs_to_one_day_only() -> None:
    start, end = day_bounds(DAY)
    assert start == DAY
    assert end == DAY + timedelta(days=1)


def test_a_naive_day_is_refused_rather_than_guessed() -> None:
    """Every datetime in this project is UTC; guessing a zone here would move the trades."""
    with pytest.raises(ValueError):
        day_bounds(datetime(2026, 10, 7))  # noqa: DTZ001 - the point of the test


def test_a_late_evening_instant_still_belongs_to_its_own_day() -> None:
    start, end = day_bounds(datetime(2026, 10, 7, 23, 59, tzinfo=UTC))
    assert start == DAY and end == DAY + timedelta(days=1)


# --- what the operator reads ---------------------------------------------------------------


def test_the_compact_form_is_signed_and_carries_the_currency() -> None:
    result = DailyResult(day=DAY, mode=TradingMode.SIGNAL, performance=_performance())
    assert result.compact == "+12.50 EUR"


def test_a_loss_is_shown_with_its_minus_sign() -> None:
    result = DailyResult(day=DAY, mode=TradingMode.SIGNAL, performance=_performance(net="-17.50"))
    assert result.compact == "-17.50 EUR"


def test_an_unknown_balance_is_said_rather_than_shown_as_zero() -> None:
    """A zero would be acted on; "unknown" sends the operator to look."""
    result = DailyResult(day=DAY, mode=TradingMode.SIGNAL, performance=_performance())
    assert result.balance is None
    assert result.balance_text == "solde inconnu"
    assert "0.00" not in result.summary()


def test_the_summary_names_the_shape_of_the_day_and_the_balance() -> None:
    result = DailyResult(
        day=DAY,
        mode=TradingMode.SIGNAL,
        performance=_performance(),
        balance=Decimal("1012.50"),
    )
    text = result.summary()
    assert "+12.50 EUR" in text
    assert "3 trade(s)" in text
    assert "2 gain(s)" in text
    assert "solde 1012.50 EUR" in text


def test_a_day_with_no_trade_says_so_plainly() -> None:
    empty = compute_performance([])
    result = DailyResult(
        day=DAY, mode=TradingMode.SIGNAL, performance=empty, balance=Decimal("1000.00")
    )
    assert "Aucun trade" in result.summary()
    assert "solde 1000.00 EUR" in result.summary()


def test_the_detail_aligns_its_values_in_a_column() -> None:
    """Read on a phone: a value column that wanders is a value column nobody compares."""
    import re

    result = DailyResult(
        day=DAY,
        mode=TradingMode.SIGNAL,
        performance=_performance(),
        balance=Decimal("1012.50"),
    )
    lines = result.detail().splitlines()
    assert len(lines) >= 5
    positions = set()
    for line in lines:
        match = re.match(r"^(.+?)\s{2,}(\S.*)$", line)
        assert match is not None, line
        positions.add(match.start(2))
    assert len(positions) == 1, f"the value column moves: {positions}"
    assert any("Solde" in line for line in lines)


def test_the_absolute_drawdown_never_carries_a_sign() -> None:
    result = DailyResult(
        day=DAY,
        mode=TradingMode.SIGNAL,
        performance=_performance(),
        balance=Decimal("1012.50"),
    )
    assert "Drawdown du jour" in result.detail()
    assert "+17.50" not in result.detail()


# --- the query ------------------------------------------------------------------------------


def _seed(engine: Engine, *, pnl: str, closed_at: datetime, mode: TradingMode) -> None:
    """One closed trade, with the version, signal and order it hangs from.

    The strategy version is reused rather than re-inserted: its `ref` is unique, and the
    tests that seed several trades are the common case.
    """
    with engine.begin() as connection:
        version_id = connection.execute(
            select(StrategyVersionRow.id).where(StrategyVersionRow.ref == "witness@1.1.0")
        ).scalar_one_or_none()
        if version_id is None:
            version_id = connection.execute(
                insert(StrategyVersionRow)
                .values(
                    ref="witness@1.1.0",
                    strategy_id="witness",
                    version="1.1.0",
                    manifest={},
                    content_hash="a" * 64,
                    first_seen_at=closed_at,
                )
                .returning(StrategyVersionRow.id)
            ).scalar_one()
        signal_id = connection.execute(
            insert(SignalRow)
            .values(
                idempotency_key=f"key-{closed_at.isoformat()}-{pnl}-{mode.value}",
                strategy_version_id=version_id,
                symbol="XAUUSD",
                timeframe="M15",
                direction="BUY",
                mode=mode,
                observed_price=2400.0,
                entry_low=2400.0,
                entry_high=2401.0,
                stop_loss=2390.0,
                take_profits=[2410.0],
                reason="test",
                indicators={},
                generated_at=closed_at,
                expires_at=closed_at,
                state=SignalState.CANDIDATE,
            )
            .returning(SignalRow.id)
        ).scalar_one()
        order_id = connection.execute(
            insert(OrderRow)
            .values(
                signal_id=signal_id,
                idempotency_key=f"order-{closed_at.isoformat()}-{pnl}-{mode.value}",
                symbol="XAUUSD",
                direction="BUY",
                volume=Decimal("0.01"),
                requested_price=Decimal("2400"),
                stop_loss=Decimal("2390"),
                take_profit=Decimal("2410"),
                mode=mode,
                state=OrderState.FILLED,
                created_at=closed_at,
                updated_at=closed_at,
            )
            .returning(OrderRow.id)
        ).scalar_one()
        position_id = connection.execute(
            insert(PositionRow)
            .values(
                order_id=order_id,
                broker_position_ticket=signal_id,
                symbol="XAUUSD",
                direction="BUY",
                volume=Decimal("0.01"),
                open_price=Decimal("2400"),
                stop_loss=Decimal("2390"),
                take_profit=Decimal("2410"),
                mode=mode,
                state=PositionState.OPEN,
                opened_at=closed_at,
                updated_at=closed_at,
            )
            .returning(PositionRow.id)
        ).scalar_one()
        connection.execute(
            insert(TradeRow).values(
                position_id=position_id,
                mode=mode,
                closed_at=closed_at,
                close_price=Decimal("2410"),
                pnl_eur=Decimal(pnl),
                risk_eur=Decimal("10"),
                exit_reason="take_profit",
            )
        )


def test_the_day_sums_only_its_own_trades(db_engine: Engine) -> None:
    _seed(db_engine, pnl="10.00", closed_at=DAY + timedelta(hours=9), mode=TradingMode.SIGNAL)
    _seed(db_engine, pnl="-4.00", closed_at=DAY + timedelta(hours=11), mode=TradingMode.SIGNAL)
    _seed(db_engine, pnl="99.00", closed_at=DAY - timedelta(hours=2), mode=TradingMode.SIGNAL)
    _seed(db_engine, pnl="50.00", closed_at=DAY + timedelta(days=1), mode=TradingMode.SIGNAL)

    result = daily_result(db_engine, DAY, mode=TradingMode.SIGNAL)

    assert result.net == Decimal("6.00")
    assert result.trades == 2


def test_paper_results_are_never_added_to_real_ones(db_engine: Engine) -> None:
    """A rehearsal is not money at risk: folding them together invents a result."""
    _seed(db_engine, pnl="10.00", closed_at=DAY + timedelta(hours=9), mode=TradingMode.SIGNAL)
    _seed(db_engine, pnl="500.00", closed_at=DAY + timedelta(hours=10), mode=TradingMode.PAPER)

    result = daily_result(db_engine, DAY, mode=TradingMode.SIGNAL)

    assert result.net == Decimal("10.00")
    assert result.trades == 1


def test_the_balance_comes_from_the_snapshot_not_from_the_trades(db_engine: Engine) -> None:
    """Summing the day onto a balance the account never held invents a figure."""
    AccountStore(db_engine).record(
        equity=Decimal("1500.00"), balance=Decimal("1490.00"), at=DAY + timedelta(hours=8)
    )
    _seed(db_engine, pnl="10.00", closed_at=DAY + timedelta(hours=9), mode=TradingMode.SIGNAL)

    result = daily_result(db_engine, DAY, mode=TradingMode.SIGNAL)

    assert result.balance == Decimal("1490.00")
    assert result.equity == Decimal("1500.00")


def test_without_a_snapshot_the_balance_is_unknown(db_engine: Engine) -> None:
    _seed(db_engine, pnl="10.00", closed_at=DAY + timedelta(hours=9), mode=TradingMode.SIGNAL)

    result = daily_result(db_engine, DAY, mode=TradingMode.SIGNAL)

    assert result.balance is None
    assert result.balance_text == "solde inconnu"


def test_no_trade_means_no_division_by_zero(db_engine: Engine) -> None:
    result = daily_result(db_engine, DAY, mode=TradingMode.SIGNAL)
    assert result.trades == 0
    assert result.net == Decimal("0")


def test_closed_trades_carries_the_strategy_reference(db_engine: Engine) -> None:
    """`SignalRow` has no ref of its own: it lives on the version row."""
    _seed(db_engine, pnl="10.00", closed_at=DAY + timedelta(hours=9), mode=TradingMode.SIGNAL)

    trades = closed_trades(db_engine, *day_bounds(DAY), mode=TradingMode.SIGNAL)

    assert [trade.strategy_ref for trade in trades] == ["witness@1.1.0"]
