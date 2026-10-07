"""Backtest-versus-production comparison (TASK-093, F-026, EF-027, R-12).

The comparison reads its numbers from the database only: production from `trades`,
the reference block from the strategy manifest persisted in `strategy_versions`. Demo and
live figures are compared as two separate lines and never aggregated (R-14).
"""

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tradingagent.analytics import Performance, Trade, compute_performance
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import OrderState, PositionState, SignalState
from tradingagent.core.timeframe import Timeframe
from tradingagent.reporting.comparison import (
    ComparisonBuilder,
    ComparisonThresholds,
    compare_strategy,
)
from tradingagent.reporting.generator import ReportGenerator
from tradingagent.reporting.schedule import Period, Window
from tradingagent.storage.account import AccountStore, ReportData
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import (
    OrderRow,
    PositionRow,
    SignalRow,
    StrategyVersionRow,
    TradeRow,
)

DAY = datetime(2026, 10, 1, tzinfo=UTC)
BASE = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)
WINDOW = Window(Period.MONTHLY, DAY, datetime(2026, 11, 1, tzinfo=UTC))


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'comparison.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


def performance(
    pnls: list[str],
    *,
    mode: TradingMode = TradingMode.DEMO,
    ref: str = "witness@1.0.0",
    risk: str = "5",
) -> Performance:
    trades = [
        Trade(
            symbol="XAUUSD",
            strategy_ref=ref,
            direction=Direction.BUY,
            timeframe=Timeframe.M15,
            mode=mode,
            opened_at=BASE + timedelta(hours=index),
            closed_at=BASE + timedelta(hours=index, minutes=30),
            pnl_eur=Decimal(pnl),
            risk_eur=Decimal(risk),
        )
        for index, pnl in enumerate(pnls)
    ]
    return compute_performance(trades)


def reference(**metrics: float) -> dict[str, Any]:
    return dict(metrics)


# --- the pure comparison ---------------------------------------------------------


def test_a_nil_gap_raises_no_alert() -> None:
    production = performance(["10", "-5"])  # net 5, win rate 0.5, drawdown 5
    comparison = compare_strategy(
        "witness@1.0.0",
        TradingMode.DEMO,
        reference(net_profit=5.0, win_rate=0.5, max_drawdown=5.0),
        production,
        ComparisonThresholds(min_production_trades=1),
    )
    assert comparison.metrics
    assert not comparison.breached
    assert comparison.alerts() == ()


def test_a_gap_below_the_threshold_raises_no_alert() -> None:
    production = performance(["10", "-5"])
    comparison = compare_strategy(
        "witness@1.0.0",
        TradingMode.DEMO,
        reference(net_profit=5.5),  # 5 / 5.5 is a 9.1 % gap, under the 25 % threshold
        production,
        ComparisonThresholds(min_production_trades=1),
    )
    net = next(metric for metric in comparison.metrics if metric.name == "net_profit")
    assert net.gap == pytest.approx(9.09, abs=0.01)
    assert not net.breached
    assert not comparison.breached


def test_a_gap_above_the_threshold_raises_an_alert() -> None:
    production = performance(["10", "-5"])  # net 5 against a reference net of 100
    comparison = compare_strategy(
        "witness@1.0.0",
        TradingMode.DEMO,
        reference(net_profit=100.0),
        production,
        ComparisonThresholds(min_production_trades=1),
    )
    net = next(metric for metric in comparison.metrics if metric.name == "net_profit")
    assert net.gap == pytest.approx(95.0)
    assert net.breached
    assert comparison.breached
    assert any("witness@1.0.0" in alert for alert in comparison.alerts())


def test_a_win_rate_drift_is_measured_in_points() -> None:
    production = performance(["10", "-5"])  # win rate 0.5
    comparison = compare_strategy(
        "witness@1.0.0",
        TradingMode.DEMO,
        reference(win_rate=0.8),
        production,
        ComparisonThresholds(min_production_trades=1),
    )
    win_rate = next(metric for metric in comparison.metrics if metric.name == "win_rate")
    assert win_rate.gap == pytest.approx(30.0)
    assert win_rate.breached  # default threshold is 15 points


def test_a_tiny_sample_is_never_turned_into_an_alert() -> None:
    production = performance(["10", "-5"])  # 2 trades
    comparison = compare_strategy(
        "witness@1.0.0",
        TradingMode.DEMO,
        reference(net_profit=1000.0),
        production,
        ComparisonThresholds(min_production_trades=5),
    )
    assert not comparison.sufficient_sample
    assert not comparison.breached
    assert any("chantillon" in alert for alert in comparison.alerts())


def test_a_missing_reference_is_reported_without_inventing_numbers() -> None:
    comparison = compare_strategy(
        "witness@1.0.0",
        TradingMode.DEMO,
        None,
        performance(["10", "-5"]),
        ComparisonThresholds(min_production_trades=1),
    )
    assert comparison.missing_reference
    assert comparison.metrics == ()
    assert not comparison.breached
    assert any("backtest" in alert for alert in comparison.alerts())


def test_thresholds_are_configurable_from_the_environment() -> None:
    thresholds = ComparisonThresholds.from_env(
        {
            "TA_COMPARISON_NET_PROFIT_GAP_PCT": "5",
            "TA_COMPARISON_MIN_PRODUCTION_TRADES": "1",
        }
    )
    assert thresholds.net_profit_gap_pct == Decimal("5")
    assert thresholds.min_production_trades == 1


# --- the database side ------------------------------------------------------------


def seed(
    engine: Engine,
    *,
    mode: TradingMode,
    pnl: Decimal,
    backtest: Mapping[str, Any] | None = None,
    ticket: int,
    ref: str = "witness@1.0.0",
) -> None:
    manifest: dict[str, Any] = {"strategy_id": "witness", "version": "1.0.0", "parameters": {}}
    if backtest is not None:
        manifest["parameters"] = {"backtest": dict(backtest)}
    with Session(engine) as session:
        version = session.query(StrategyVersionRow).filter_by(ref=ref).one_or_none()
        if version is None:
            version = StrategyVersionRow(
                ref=ref,
                strategy_id="witness",
                version="1.0.0",
                manifest=manifest,
                content_hash="a" * 64,
                first_seen_at=DAY,
            )
            session.add(version)
            session.flush()
        signal = SignalRow(
            idempotency_key=f"sig-{ticket}",
            strategy_version_id=version.id,
            symbol="XAUUSD",
            timeframe=Timeframe.M15,
            direction=Direction.BUY,
            mode=mode,
            observed_price=2650.0,
            entry_low=2649.5,
            entry_high=2650.5,
            stop_loss=2647.5,
            take_profits=[2652.5],
            reason="test",
            indicators={},
            generated_at=BASE,
            expires_at=BASE + timedelta(hours=1),
            state=SignalState.CLOSED,
        )
        session.add(signal)
        session.flush()
        order = OrderRow(
            idempotency_key=f"ord-{ticket}",
            signal_id=signal.id,
            symbol="XAUUSD",
            direction=Direction.BUY,
            volume=Decimal("0.01"),
            requested_price=2650.0,
            stop_loss=2647.5,
            take_profit=2652.5,
            mode=mode,
            state=OrderState.FILLED,
            created_at=BASE,
            updated_at=BASE,
        )
        session.add(order)
        session.flush()
        position = PositionRow(
            broker_position_ticket=ticket,
            order_id=order.id,
            symbol="XAUUSD",
            direction=Direction.BUY,
            volume=Decimal("0.01"),
            open_price=2650.0,
            stop_loss=2647.5,
            take_profit=2652.5,
            mode=mode,
            state=PositionState.CLOSED,
            opened_at=BASE,
            updated_at=BASE,
        )
        session.add(position)
        session.flush()
        session.add(
            TradeRow(
                position_id=position.id,
                mode=mode,
                closed_at=BASE + timedelta(hours=1),
                close_price=2652.0,
                pnl_eur=pnl,
                risk_eur=Decimal("10.00"),
                exit_reason="take_profit",
            )
        )
        session.commit()


def test_the_reference_block_comes_from_the_strategy_manifest(engine: Engine) -> None:
    for index in range(5):
        seed(
            engine,
            mode=TradingMode.DEMO,
            pnl=Decimal("10.00"),
            backtest={"net_profit": 55.0, "trades": 12},
            ticket=100 + index,
        )
    report = ComparisonBuilder(engine, ComparisonThresholds()).build(WINDOW.start, WINDOW.end)
    assert len(report.comparisons) == 1
    comparison = report.comparisons[0]
    assert comparison.reference_trades == 12
    net = next(metric for metric in comparison.metrics if metric.name == "net_profit")
    assert net.reference == pytest.approx(55.0)
    assert net.production == pytest.approx(50.0)


def test_demo_and_live_are_compared_separately_and_never_aggregated(engine: Engine) -> None:
    seed(engine, mode=TradingMode.DEMO, pnl=Decimal("10.00"), ticket=201)
    seed(engine, mode=TradingMode.LIVE, pnl=Decimal("-4.00"), ticket=202)
    report = ComparisonBuilder(engine, ComparisonThresholds()).build(WINDOW.start, WINDOW.end)

    modes = {comparison.mode: comparison for comparison in report.comparisons}
    assert set(modes) == {TradingMode.DEMO, TradingMode.LIVE}
    assert modes[TradingMode.DEMO].production_trades == 1
    assert modes[TradingMode.LIVE].production_trades == 1
    assert modes[TradingMode.DEMO].production.net_profit == Decimal("10.00")
    assert modes[TradingMode.LIVE].production.net_profit == Decimal("-4.00")
    assert any("R-14" in line for line in report.render())


def test_a_breach_becomes_an_alert_in_the_report(engine: Engine) -> None:
    for index in range(5):
        seed(
            engine,
            mode=TradingMode.DEMO,
            pnl=Decimal("1.00"),
            backtest={"net_profit": 500.0},
            ticket=300 + index,
        )
    report = ComparisonBuilder(engine, ComparisonThresholds()).build(WINDOW.start, WINDOW.end)
    assert report.alerts
    assert any("witness@1.0.0" in alert for alert in report.alerts)


def test_a_strategy_without_a_reference_is_listed_with_an_explicit_alert(engine: Engine) -> None:
    seed(engine, mode=TradingMode.DEMO, pnl=Decimal("10.00"), ticket=401)
    report = ComparisonBuilder(engine, ComparisonThresholds(min_production_trades=1)).build(
        WINDOW.start, WINDOW.end
    )
    comparison = report.comparisons[0]
    assert comparison.missing_reference
    assert any("backtest" in alert for alert in report.alerts)


def test_the_comparison_is_present_in_the_monthly_report(engine: Engine) -> None:
    for index in range(5):
        seed(
            engine,
            mode=TradingMode.DEMO,
            pnl=Decimal("1.00"),
            backtest={"net_profit": 500.0},
            ticket=500 + index,
        )
    content = ReportGenerator(ReportData(engine), AccountStore(engine)).build(WINDOW)
    assert "Comparaison backtest / production" in content
    assert "witness@1.0.0" in content
    assert "ALERTE" in content


def test_the_daily_report_stays_free_of_the_comparison(engine: Engine) -> None:
    seed(engine, mode=TradingMode.DEMO, pnl=Decimal("10.00"), ticket=601)
    daily = Window(Period.DAILY, BASE.replace(hour=0), BASE.replace(hour=0) + timedelta(days=1))
    content = ReportGenerator(ReportData(engine), AccountStore(engine)).build(daily)
    assert "Comparaison backtest" not in content
