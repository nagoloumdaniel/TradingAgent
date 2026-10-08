"""The read-only query layer, exercised against a migrated throwaway database."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Engine, delete, insert
from tests.web import seed

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import HaltAction, HaltSource, Severity
from tradingagent.storage.models import HaltCommandRow, StrategyRegistryRow
from tradingagent.web import queries
from tradingagent.web.app import AGENT_CONFIG


def test_trades_come_from_the_reporting_reader(populated: seed.Seeded, engine: Engine) -> None:
    trades = queries.all_trades(engine)
    assert [trade.closed_at for trade in trades] == sorted(trade.closed_at for trade in trades)
    assert sum((trade.pnl_eur for trade in trades), Decimal(0)) == seed.TOTAL_PNL
    assert {trade.symbol for trade in trades} == {seed.XAU, seed.BTC}


def test_overview_totals_match_the_stated_dataset(populated: seed.Seeded, engine: Engine) -> None:
    overview = queries.overview(engine, seed.NOW)
    assert overview.total.trades == 5
    assert overview.total.wins == 3
    assert overview.total.losses == 2
    assert overview.total.win_rate == Decimal("0.6")
    assert overview.total.net_profit == seed.TOTAL_PNL
    assert overview.total.max_drawdown == seed.MAX_DRAWDOWN
    assert overview.pnl_day == seed.DAY_PNL
    assert overview.pnl_week == seed.WEEK_PNL
    assert overview.pnl_month == seed.MONTH_PNL
    assert overview.halt.halted is False


def test_markets_are_split_and_sum_to_the_total(populated: seed.Seeded, engine: Engine) -> None:
    overview = queries.overview(engine, seed.NOW)
    by_market = {summary.market: summary for summary in overview.markets}
    assert set(by_market) == {seed.XAU, seed.BTC}
    assert by_market[seed.XAU].performance.net_profit == seed.XAU_PNL
    assert by_market[seed.BTC].performance.net_profit == seed.BTC_PNL
    assert sum((summary.performance.net_profit for summary in overview.markets), Decimal(0)) == (
        overview.total.net_profit
    )
    assert by_market[seed.XAU].open_positions == 1
    assert by_market[seed.XAU].notional == seed.XAU_NOTIONAL
    assert by_market[seed.BTC].notional == seed.BTC_NOTIONAL


def test_account_figures_read_the_snapshots(populated: seed.Seeded, engine: Engine) -> None:
    account = queries.account_figures(engine, seed.NOW)
    assert account.balance == seed.BALANCE
    assert account.equity == seed.EQUITY
    assert account.peak_equity == seed.EQUITY_PEAK
    assert account.drawdown == seed.DRAWDOWN
    assert account.at == seed.NOW - timedelta(hours=1)


def test_account_figures_on_an_empty_database(empty_engine: Engine) -> None:
    account = queries.account_figures(empty_engine, seed.NOW)
    assert account.balance is None
    assert account.equity is None
    assert account.drawdown is None


def test_open_positions_carry_volume_and_notional(populated: seed.Seeded, engine: Engine) -> None:
    positions = queries.open_positions(engine, seed.NOW)
    assert [position.symbol for position in positions] == [seed.XAU, seed.BTC]
    notionals = {position.symbol: position.notional for position in positions}
    assert notionals[seed.XAU] == seed.XAU_NOTIONAL
    assert notionals[seed.BTC] == seed.BTC_NOTIONAL
    assert [position.age for position in positions] == [timedelta(hours=2), timedelta(hours=1)]


def test_filters_select_on_market_strategy_and_mode(populated: seed.Seeded, engine: Engine) -> None:
    entries = queries.all_trade_entries(engine)
    assert len(entries) == 5
    assert len(queries.TradeFilters(market=seed.BTC).apply_entries(entries)) == 2
    assert len(queries.TradeFilters(strategy=seed.WITNESS).apply_entries(entries)) == 3
    assert len(queries.TradeFilters(mode=TradingMode.DEMO).apply_entries(entries)) == 1
    assert (
        len(
            queries.TradeFilters(
                market=seed.XAU, strategy=seed.WITNESS, mode=TradingMode.PAPER
            ).apply_entries(entries)
        )
        == 3
    )
    assert queries.selectable_markets(entry.trade for entry in entries) == [seed.BTC, seed.XAU]


def test_trade_detail_carries_the_signal_and_the_risk_verdict(
    populated: seed.Seeded, engine: Engine
) -> None:
    detail = queries.trade_detail(engine, populated.losing_signal_id)
    assert detail is not None
    assert detail.trade.symbol == seed.XAU
    assert detail.trade.direction is Direction.SELL
    assert detail.trade.pnl_eur == Decimal("-4.00")
    assert detail.signal_reason == "croisement de moyennes confirmé par le volume"
    assert detail.risk_outcome is not None
    assert detail.entry_low == 2648.0
    assert detail.take_profits == (2660.0, 2670.0)
    assert detail.executions[0].broker_deal_ticket == 900000 + populated.losing_signal_id


def test_trade_detail_is_none_for_an_unknown_signal(empty_engine: Engine) -> None:
    assert queries.trade_detail(empty_engine, 4242) is None


def test_strategies_merge_the_registry_and_the_realized_history(
    populated: seed.Seeded, engine: Engine
) -> None:
    views = {view.ref: view for view in queries.strategies(engine)}
    assert set(views) == {seed.WITNESS, seed.BREAKOUT}
    witness = views[seed.WITNESS]
    assert witness.status is not None
    assert witness.status.value == "live"
    assert witness.performance.net_profit == seed.XAU_PNL
    assert witness.last_backtest is not None
    assert witness.last_backtest.metrics["sharpe"] == 1.4
    assert [validation.stage.value for validation in witness.validations] == ["walk_forward"]
    breakout = views[seed.BREAKOUT]
    assert breakout.last_backtest is None
    assert breakout.validations[0].passed is False


def test_a_traded_reference_absent_from_the_registry_is_still_shown(
    populated: seed.Seeded, engine: Engine
) -> None:
    with engine.begin() as connection:
        connection.execute(
            delete(StrategyRegistryRow).where(StrategyRegistryRow.ref == seed.BREAKOUT)
        )
    views = {view.ref: view for view in queries.strategies(engine)}
    assert set(views) == {seed.WITNESS, seed.BREAKOUT}
    unregistered = views[seed.BREAKOUT]
    assert unregistered.status is None
    assert unregistered.origin is None
    assert unregistered.performance.net_profit == seed.BTC_PNL


def test_ai_lab_reads_analyses_and_proposals(populated: seed.Seeded, engine: Engine) -> None:
    lab = queries.ai_lab(engine)
    assert len(lab.analyses) == 1
    assert lab.analyses[0].model == "claude-sonnet-4-5"
    assert lab.analyses[0].cost_eur == Decimal("0.012")
    assert len(lab.proposals) == 1
    assert lab.proposals[0].status == "proposed"
    assert len(lab.validations) == 2
    assert len(lab.backtests) == 1


def test_risk_view_reports_exposure_limits_and_the_halt_history(
    populated: seed.Seeded, engine: Engine
) -> None:
    view = queries.risk_view(engine, seed.NOW, AGENT_CONFIG)
    assert view.halt.halted is False
    assert view.halted_markets == ("BTCUSD",)
    assert view.halted_pairs == ()
    assert view.refusals.count == 1
    assert view.refusals.avoided_risk_eur == seed.REFUSED_RISK
    exposures = {exposure.market: exposure for exposure in view.exposures}
    assert exposures[seed.XAU].notional == seed.XAU_NOTIONAL
    assert exposures[seed.BTC].notional == seed.BTC_NOTIONAL
    # The two lists that grow without bound are pages now: counted in SQL, ten rows a time.
    assert [command.scope for command in view.history.rows] == ["market:BTCUSD"]
    assert view.history.rows[0].reason == "volatilité excessive"
    assert {"broker_disconnected", "clock_mismatch", "cycle"} <= {
        event.kind for event in view.events.rows
    }


def test_risk_limits_are_read_from_the_agent_configuration(
    populated: seed.Seeded, engine: Engine
) -> None:
    limits = queries.risk_limits(AGENT_CONFIG)
    profiles = {entry.profile: entry for entry in limits}
    assert set(profiles) == {"simulé", "réel"}
    assert profiles["simulé"].risk_per_trade_pct == Decimal("0.5")
    assert profiles["simulé"].max_open_positions == 2
    assert profiles["réel"].risk_per_trade_pct == Decimal("2")
    assert profiles["réel"].currency == "EUR"
    assert profiles["réel"].reference_capital == Decimal("100")


def test_risk_limits_degrade_to_empty_when_the_file_is_missing(tmp_path: Path) -> None:
    assert queries.risk_limits(tmp_path / "absent.yaml") == ()


def test_risk_limits_degrade_to_empty_on_an_invalid_profile(tmp_path: Path) -> None:
    broken = tmp_path / "agent.yaml"
    broken.write_text("risk:\n  simulated:\n    risk_per_trade_pct: 0.5\n", encoding="utf-8")
    assert queries.risk_limits(broken) == ()


def test_a_global_halt_is_reported_as_halted(populated: seed.Seeded, engine: Engine) -> None:
    assert queries.halt_status(engine).halted is False
    with engine.begin() as connection:
        connection.execute(
            insert(HaltCommandRow).values(
                scope="global",
                action=HaltAction.HALT,
                close_positions=True,
                source=HaltSource.AUTOMATIC,
                reason="perte quotidienne dépassée",
                actor="guardian",
                occurred_at=seed.NOW,
            )
        )
    status = queries.halt_status(engine)
    assert status.halted is True
    assert status.close_positions is True
    assert any("perte quotidienne dépassée" in reason for reason in status.reasons)


def test_market_health_reports_candle_freshness(populated: seed.Seeded, engine: Engine) -> None:
    health = {entry.symbol: entry for entry in queries.market_health(engine, seed.NOW)}
    assert set(health) == {seed.XAU, seed.BTC}
    assert health[seed.XAU].candle_age == timedelta(hours=1)
    assert health[seed.BTC].candle_age == timedelta(hours=30)
    assert health[seed.XAU].last_event_kind is not None


def test_latencies_are_measured_from_stored_events(populated: seed.Seeded, engine: Engine) -> None:
    latencies = {view.label: view.stats for view in queries.latency_views(engine)}
    assert latencies["Signal → ordre envoyé"].median_ms == 1000.0
    assert latencies["Ordre envoyé → accepté"].median_ms == 500.0
    assert latencies["Signal → exécuté"].worst_ms == 2000.0


def test_database_health_reports_a_reachable_database(empty_engine: Engine) -> None:
    health = queries.database_health(empty_engine)
    assert health.ok is True
    assert health.dialect == "sqlite"
    assert health.error is None


def test_system_view_assembles_every_probe(populated: seed.Seeded, engine: Engine) -> None:
    view = queries.system_view(engine, seed.NOW)
    assert view.database.ok is True
    assert view.halt.halted is False
    assert {entry.symbol for entry in view.markets} == {seed.XAU, seed.BTC}
    assert view.telemetry.total == 4
    assert {event.kind for event in view.errors.rows} == {"broker_disconnected", "clock_mismatch"}


def test_recent_events_can_be_restricted_to_warnings_and_above(
    populated: seed.Seeded, engine: Engine
) -> None:
    everything = queries.recent_events(engine)
    warnings = queries.recent_events(engine, minimum=Severity.WARNING)
    assert len(everything) == 3
    assert len(warnings) == 2
    assert all(event.severity is not Severity.INFO for event in warnings)
    assert warnings[0].occurred_at > warnings[1].occurred_at


def test_recent_events_since_a_moment(populated: seed.Seeded, engine: Engine) -> None:
    since = seed.NOW - timedelta(hours=3)
    assert [event.kind for event in queries.recent_events(engine, since=since)] == [
        "cycle",
        "clock_mismatch",
    ]


def test_reports_are_read_verbatim(populated: seed.Seeded, engine: Engine) -> None:
    stored = queries.reports(engine)
    assert len(stored) == 1
    assert stored[0].content == seed.REPORT_CONTENT
    assert stored[0].sent_at is None
    assert queries.report_by_id(engine, populated.report_id) == stored[0]
    assert queries.report_by_id(engine, 9999) is None


def test_windows_are_utc_midnights() -> None:
    moment = datetime(2026, 10, 7, 13, 45, tzinfo=UTC)
    assert queries.day_start(moment) == datetime(2026, 10, 7, tzinfo=UTC)
    assert queries.week_start(moment) == datetime(2026, 10, 5, tzinfo=UTC)
    assert queries.month_start(moment) == datetime(2026, 10, 1, tzinfo=UTC)
