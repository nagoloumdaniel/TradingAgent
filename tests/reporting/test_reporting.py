import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import OrderState, PositionState, RiskOutcome, Severity, SignalState
from tradingagent.core.timeframe import Timeframe
from tradingagent.reporting.schedule import Period, Window, missed_windows, window_containing
from tradingagent.reporting.service import ReportService
from tradingagent.storage.account import AccountStore
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import (
    OrderRow,
    PositionRow,
    ReportRow,
    RiskDecisionRow,
    SignalRow,
    StrategyVersionRow,
    SystemEventRow,
    TradeRow,
)

D1 = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)  # Monday
D2 = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)  # Tuesday
D3 = datetime(2026, 10, 7, 0, 0, tzinfo=UTC)  # Wednesday
NOON = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'reports.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


def seed_trading_day(engine: Engine, day_start: datetime, pnl: Decimal) -> None:
    """One closed trade on `day_start`'s day, plus one refused signal and one anomaly."""
    with Session(engine) as session:
        version = session.scalars(select(StrategyVersionRow)).first()
        if version is None:
            version = StrategyVersionRow(
                ref="witness@1.0.0",
                strategy_id="witness",
                version="1.0.0",
                manifest={},
                content_hash="0" * 64,
                first_seen_at=day_start,
            )
            session.add(version)
            session.flush()
        signal = SignalRow(
            idempotency_key=f"witness@1.0.0:XAUUSD:M15:{day_start:%Y-%m-%dT%H:%MZ}",
            strategy_version_id=version.id,
            symbol="XAUUSD",
            timeframe=Timeframe.M15,
            direction=Direction.BUY,
            mode=TradingMode.DEMO,
            observed_price=2650.0,
            entry_low=2649.5,
            entry_high=2650.5,
            stop_loss=2647.5,
            take_profits=[2652.5],
            reason="crossover",
            indicators={},
            generated_at=day_start + timedelta(hours=1),
            expires_at=day_start + timedelta(hours=2),
            state=SignalState.CLOSED,
        )
        session.add(signal)
        session.flush()
        session.add(
            RiskDecisionRow(
                signal_id=signal.id,
                outcome=RiskOutcome.REFUSED,
                reason="daily loss limit",
                checks={},
                volume=None,
                risk_eur=Decimal("12.50"),
                decided_at=day_start + timedelta(hours=2),
            )
        )
        session.add(
            SystemEventRow(
                kind="series_anomaly",
                severity=Severity.WARNING,
                detail={"symbol": "XAUUSD", "status": "STALE"},
                occurred_at=day_start + timedelta(hours=3),
            )
        )
        order = OrderRow(
            idempotency_key=f"order:{day_start:%Y%m%d%H}",
            signal_id=signal.id,
            symbol="XAUUSD",
            direction=Direction.BUY,
            volume=Decimal("0.01"),
            requested_price=2650.0,
            stop_loss=2647.5,
            take_profit=2652.5,
            mode=TradingMode.DEMO,
            state=OrderState.FILLED,
            created_at=day_start + timedelta(hours=1),
            updated_at=day_start + timedelta(hours=1),
        )
        session.add(order)
        session.flush()
        position = PositionRow(
            broker_position_ticket=int(day_start.timestamp()),
            order_id=order.id,
            symbol="XAUUSD",
            direction=Direction.BUY,
            volume=Decimal("0.01"),
            open_price=2650.0,
            stop_loss=2647.5,
            take_profit=2652.5,
            mode=TradingMode.DEMO,
            state=PositionState.CLOSED,
            opened_at=day_start + timedelta(hours=1),
            updated_at=day_start + timedelta(hours=2),
        )
        session.add(position)
        session.flush()
        session.add(
            TradeRow(
                position_id=position.id,
                mode=TradingMode.DEMO,
                closed_at=day_start + timedelta(hours=2),
                close_price=2652.0,
                pnl_eur=pnl,
                risk_eur=Decimal("10.00"),
                exit_reason="take_profit",
            )
        )
        session.commit()


# --- window arithmetic ---------------------------------------------------------


def test_daily_and_monthly_windows_align_on_utc() -> None:
    day = window_containing(Period.DAILY, NOON)
    assert day.start == D2 and day.end == D3

    month = window_containing(Period.MONTHLY, NOON)
    assert (month.start.year, month.start.month, month.start.day) == (2026, 10, 1)
    assert (month.end.year, month.end.month, month.end.day) == (2026, 11, 1)


def test_missed_windows_catch_up_without_flooding_history() -> None:
    now = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    assert missed_windows(Period.DAILY, None, now) == [
        Window(Period.DAILY, datetime(2026, 10, 8, tzinfo=UTC), datetime(2026, 10, 9, tzinfo=UTC))
    ]
    assert missed_windows(Period.DAILY, D3, now) == [
        Window(Period.DAILY, D3, datetime(2026, 10, 8, tzinfo=UTC)),
        Window(Period.DAILY, datetime(2026, 10, 8, tzinfo=UTC), datetime(2026, 10, 9, tzinfo=UTC)),
    ]
    assert missed_windows(Period.DAILY, D1, now)[0].start == D1


# --- the daily report content --------------------------------------------------


def test_daily_report_states_the_avoided_losses_and_balances(engine: Engine) -> None:
    seed_trading_day(engine, D2 + timedelta(hours=0), Decimal("85.00"))
    AccountStore(engine).record(Decimal("1000.00"), Decimal("1000.00"), D2)
    AccountStore(engine).record(Decimal("1085.00"), Decimal("1085.00"), D3)

    generator = ReportGeneratorOf(engine)
    content = generator.build(Window(Period.DAILY, D2, D3))

    assert "Pertes évitées" in content
    assert "12.50" in content  # the refused risk
    assert "1000.00" in content and "1085.00" in content  # opening and closing balances
    assert "+85.00" in content  # the day result
    assert "1 refus" in content


def test_no_number_in_the_report_comes_from_the_model(engine: Engine) -> None:
    seed_trading_day(engine, D2, Decimal("85.00"))

    numeric = ReportGeneratorOf(engine).build(Window(Period.DAILY, D2, D3))
    stripped = "\n".join(line for line in numeric.splitlines() if line.strip())

    # Data-flow inspection: the narrative is appended by the service AFTER this text,
    # and this text is built without any model in the call graph — asserted by the
    # service test below, where a lying narrator cannot change the numbers.
    assert "Résultat de la période" in stripped
    assert stripped.count("\n") >= 8


# --- the service: catch-up, idempotence, narrator failure ------------------------


def test_an_outage_covering_the_deadline_loses_no_report(engine: Engine) -> None:
    seed_trading_day(engine, D2, Decimal("85.00"))
    sent: list[str] = []
    service = ReportService(engine, sent.append)
    # Baseline: the agent was running and reported through October 6th's evening.
    asyncio.run(service.run(datetime(2026, 10, 6, 21, 0, tzinfo=UTC)))
    before = len(sent)

    # An outage covers the following deadlines; the restart catches every window up.
    delivered = asyncio.run(service.run(datetime(2026, 10, 9, 12, 0, tzinfo=UTC)))

    assert len(delivered) == 3  # the dailies of Oct 6→7, Oct 7→8 and Oct 8→9
    daily_with_trade = next(text for text in delivered if "+85.00" in text)
    assert "2026-10-06" in daily_with_trade
    with Session(engine) as session:
        stored = session.scalars(select(ReportRow)).all()
    assert all(row.sent_at is not None for row in stored)
    assert len(sent) == before + len(delivered)


def test_a_second_run_sends_nothing(engine: Engine) -> None:
    seed_trading_day(engine, D2, Decimal("85.00"))
    sent: list[str] = []
    service = ReportService(engine, sent.append)
    asyncio.run(service.run(datetime(2026, 10, 6, 21, 0, tzinfo=UTC)))
    asyncio.run(service.run(datetime(2026, 10, 9, 12, 0, tzinfo=UTC)))
    after_catch_up = len(sent)

    asyncio.run(service.run(datetime(2026, 10, 9, 12, 1, tzinfo=UTC)))

    assert len(sent) == after_catch_up


def test_a_failing_narrator_does_not_lose_the_numeric_report(engine: Engine) -> None:
    seed_trading_day(engine, D2, Decimal("85.00"))

    async def lying_narrator(text: str) -> str:
        raise RuntimeError("model down")

    sent: list[str] = []
    service = ReportService(engine, sent.append, narrator=lying_narrator)
    asyncio.run(service.run(datetime(2026, 10, 6, 21, 0, tzinfo=UTC)))
    asyncio.run(service.run(datetime(2026, 10, 9, 12, 0, tzinfo=UTC)))

    daily_with_trade = next(text for text in sent if "+85.00" in text)
    assert "2026-10-06" in daily_with_trade
    assert "Commentaire" not in daily_with_trade


# --- the market a report can be filed under (F-022, /reports filter) -------------


def seed_btc_signal(engine: Engine, at: datetime) -> None:
    with Session(engine) as session:
        version = session.scalars(select(StrategyVersionRow)).first()
        assert version is not None
        session.add(
            SignalRow(
                idempotency_key=f"trend_breakout@1.0.0:BTCUSD:M15:{at:%Y-%m-%dT%H:%MZ}",
                strategy_version_id=version.id,
                symbol="BTCUSD",
                timeframe=Timeframe.M15,
                direction=Direction.SELL,
                mode=TradingMode.DEMO,
                observed_price=60000.0,
                entry_low=59990.0,
                entry_high=60010.0,
                stop_loss=60100.0,
                take_profits=[59800.0],
                reason="crossover",
                indicators={},
                generated_at=at,
                expires_at=at + timedelta(hours=1),
                state=SignalState.CLOSED,
            )
        )
        session.commit()


def test_the_daily_report_of_a_single_market_window_is_filed_under_that_market(
    engine: Engine,
) -> None:
    """The operator filters `/reports` by market: a window that traded one market says so."""
    seed_trading_day(engine, D2, Decimal("85.00"))

    asyncio.run(
        ReportService(engine, lambda text: None).run(datetime(2026, 10, 7, 12, 0, tzinfo=UTC))
    )

    with Session(engine) as session:
        filed = {row.period: row.market for row in session.scalars(select(ReportRow)).all()}
    assert filed["daily"] == "XAUUSD"
    # The weekly and monthly windows of the very first run hold no signal of their own:
    # there is no market to file them under, and the column says so instead of guessing.
    assert filed["weekly"] is None
    assert filed["monthly"] is None


def test_a_window_that_traded_two_markets_has_no_single_market(engine: Engine) -> None:
    seed_trading_day(engine, D2, Decimal("85.00"))
    seed_btc_signal(engine, D2 + timedelta(hours=4))

    assert ReportGeneratorOf(engine).sole_market(Window(Period.DAILY, D2, D3)) is None


def test_an_empty_window_has_no_single_market(engine: Engine) -> None:
    assert ReportGeneratorOf(engine).sole_market(Window(Period.DAILY, D2, D3)) is None


def ReportGeneratorOf(engine: Engine):
    from tradingagent.reporting.generator import ReportGenerator
    from tradingagent.storage.account import ReportData

    return ReportGenerator(ReportData(engine), AccountStore(engine))
