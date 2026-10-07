"""A representative database for the dashboard tests.

Built by direct inserts rather than through the runtime, because the dashboard reads rows —
it does not care which component wrote them. Every figure asserted in the tests is stated
here explicitly, so a wrong rendering is a failing test and not a coincidence.

One signal produces at most one order, one position and one trade: the chains below respect
that, so ``trade_detail`` keyed on a signal id is unambiguous.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, Engine, insert, select

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import (
    AnalysisKind,
    ExecutionEventKind,
    HaltAction,
    HaltSource,
    OrderState,
    PositionState,
    ProposalStatus,
    RiskOutcome,
    Severity,
    SignalState,
    StrategyStatus,
    ValidationStage,
)
from tradingagent.core.timeframe import Timeframe
from tradingagent.storage.daily import DailyPerformanceStore
from tradingagent.storage.models import (
    AccountSnapshotRow,
    AiAnalysisRow,
    AiProposalRow,
    BacktestRunRow,
    CandleRow,
    ExecutionEventRow,
    ExecutionRow,
    HaltCommandRow,
    OrderRow,
    PositionRow,
    ReportRow,
    RiskDecisionRow,
    SignalRow,
    StrategyRegistryRow,
    StrategyVersionRow,
    SystemEventRow,
    TradeRow,
    ValidationRunRow,
)

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
WITNESS = "witness@1.0.0"
BREAKOUT = "trend_breakout@1.0.0"
XAU = "XAUUSD"
BTC = "BTCUSD"

# Stated once, asserted everywhere: the exact figures this dataset must produce.
BALANCE = Decimal("1000.00")
EQUITY = Decimal("1012.00")
EQUITY_PEAK = Decimal("1050.00")
DRAWDOWN = Decimal("38.00")
XAU_NOTIONAL = Decimal("53.00")
BTC_NOTIONAL = Decimal("620.00")
REFUSED_RISK = Decimal("6.00")
REPORT_CONTENT = "Rapport quotidien\nRésultat de la période : +15.75 EUR"

# Realized P&L of the chains below, written out rather than summed, so a change to the
# dataset is visible as a failing assertion instead of silently moving the expectation.
#   day   2026-10-07          : +7.25 - 4.00                       = 3.25
#   week  2026-10-05 onwards  : +12.50 + 7.25 - 4.00               = 15.75
#   month 2026-10-01 onwards  : -2.00 + 12.50 + 7.25 - 4.00        = 13.75
#   total                     : the above + 3.00 on 2026-09-20     = 16.75
DAY_PNL = Decimal("3.25")
WEEK_PNL = Decimal("15.75")
MONTH_PNL = Decimal("13.75")
TOTAL_PNL = Decimal("16.75")
XAU_PNL = Decimal("11.50")  # 12.50 - 4.00 + 3.00
BTC_PNL = Decimal("5.25")  # 7.25 - 2.00
MAX_DRAWDOWN = Decimal("4.00")  # from the realized peak of 20.75 down to 16.75

# Chains: (market, strategy, direction, generated_at, closed_at or None, pnl, risk, mode).
_CLOSED_CHAINS: tuple[tuple[Any, ...], ...] = (
    (
        XAU,
        WITNESS,
        Direction.BUY,
        timedelta(days=1, hours=4),
        timedelta(days=1, hours=2),
        Decimal("12.50"),
        Decimal("5.00"),
        TradingMode.PAPER,
    ),
    (
        BTC,
        BREAKOUT,
        Direction.BUY,
        timedelta(hours=7, minutes=30),
        timedelta(hours=5, minutes=30),
        Decimal("7.25"),
        Decimal("6.00"),
        TradingMode.DEMO,
    ),
    (
        XAU,
        WITNESS,
        Direction.SELL,
        timedelta(hours=6),
        timedelta(hours=3),
        Decimal("-4.00"),
        Decimal("5.00"),
        TradingMode.PAPER,
    ),
    (
        BTC,
        BREAKOUT,
        Direction.BUY,
        timedelta(days=6, hours=9),
        timedelta(days=6, hours=7),
        Decimal("-2.00"),
        Decimal("6.00"),
        TradingMode.PAPER,
    ),
    (
        XAU,
        WITNESS,
        Direction.BUY,
        timedelta(days=17),
        timedelta(days=17, hours=-2),
        Decimal("3.00"),
        Decimal("5.00"),
        TradingMode.PAPER,
    ),
)


@dataclass(frozen=True)
class Seeded:
    """The row ids a test needs to build a link or a follow-up insert."""

    xau_signal_id: int
    btc_signal_id: int
    losing_signal_id: int
    refused_signal_id: int
    report_id: int
    signal_generated_at: datetime


def seed(engine: Engine) -> Seeded:
    with engine.begin() as connection:
        versions = {
            WITNESS: _one(
                connection,
                StrategyVersionRow,
                ref=WITNESS,
                strategy_id="witness",
                version="1.0.0",
                manifest={"ref": WITNESS, "timeframes": ["H1"]},
                content_hash="a" * 64,
                first_seen_at=NOW - timedelta(days=40),
            ),
            BREAKOUT: _one(
                connection,
                StrategyVersionRow,
                ref=BREAKOUT,
                strategy_id="trend_breakout",
                version="1.0.0",
                manifest={"ref": BREAKOUT, "timeframes": ["H1"]},
                content_hash="b" * 64,
                first_seen_at=NOW - timedelta(days=40),
            ),
        }

        signals: list[int] = []
        for market, ref, direction, opened, closed, pnl, risk, mode in _CLOSED_CHAINS:
            signals.append(
                _chain(
                    connection,
                    versions[ref],
                    market,
                    direction,
                    NOW - opened,
                    NOW - closed,
                    pnl,
                    risk,
                    mode,
                )
            )
        xau_signal, btc_signal, losing_signal, _, _ = signals

        # Two positions still open, one per market.
        _chain(
            connection,
            versions[WITNESS],
            XAU,
            Direction.BUY,
            NOW - timedelta(hours=2),
            None,
            None,
            Decimal("5.00"),
            TradingMode.PAPER,
            volume=Decimal("0.02"),
            open_price=2650.0,
        )
        _chain(
            connection,
            versions[BREAKOUT],
            BTC,
            Direction.SELL,
            NOW - timedelta(hours=1),
            None,
            None,
            Decimal("6.00"),
            TradingMode.PAPER,
            volume=Decimal("0.01"),
            open_price=62000.0,
        )

        # A signal the risk engine refused: it has no order, and its carried risk is the
        # loss that was avoided (F-011).
        refused_signal = _signal(
            connection, versions[WITNESS], XAU, Direction.BUY, NOW - timedelta(hours=3)
        )
        connection.execute(
            insert(RiskDecisionRow).values(
                signal_id=refused_signal,
                outcome=RiskOutcome.REFUSED,
                reason="plafond de positions atteint",
                checks={"max_open_positions": False},
                volume=None,
                risk_eur=REFUSED_RISK,
                margin_eur=None,
                decided_at=NOW - timedelta(hours=3),
            )
        )

        _snapshots(connection)
        _events(connection)
        _halt(connection)
        _candles(connection)
        report_id = _report(connection)
        _registry(connection)
        _research(connection)
        _ai(connection, xau_signal)

        seeded = Seeded(
            xau_signal_id=xau_signal,
            btc_signal_id=btc_signal,
            losing_signal_id=losing_signal,
            refused_signal_id=refused_signal,
            report_id=report_id,
            signal_generated_at=NOW - timedelta(days=1, hours=4),
        )
    _daily(engine)
    return seeded


def add_chain(
    engine: Engine,
    *,
    ref: str = WITNESS,
    market: str = XAU,
    direction: Direction = Direction.BUY,
    generated_at: datetime,
    closed: bool = True,
    with_execution: bool = True,
) -> int:
    """Append one more signal chain to an already-seeded database, and return its signal id.

    The dashboard tests need shapes the representative dataset deliberately does not carry:
    a closed trade with no fill, a position that never closed. Every table here is
    append-only — a test appends rather than deletes — and this helper keeps the row shape
    in one place instead of duplicating it in a test.
    """
    risk = Decimal("5.00")
    with engine.begin() as connection:
        version_id = connection.execute(
            select(StrategyVersionRow.id).where(StrategyVersionRow.ref == ref)
        ).scalar_one()
        return _chain(
            connection,
            int(version_id),
            market,
            direction,
            generated_at,
            generated_at + timedelta(hours=1) if closed else None,
            Decimal("1.00") if closed else None,
            risk,
            TradingMode.PAPER,
            with_execution=with_execution,
        )


def _daily(engine: Engine) -> None:
    """The daily aggregate table, written by its own store — the dashboard only reads it.

    It mirrors ``_CLOSED_CHAINS`` exactly, so the test can assert that the fast path and the
    trades table tell the same story.
    """
    store = DailyPerformanceStore(engine)
    for days_ago, mode, market, ref, pnl, risk in (
        (17, TradingMode.PAPER, XAU, WITNESS, Decimal("3.00"), Decimal("5.00")),
        (6, TradingMode.PAPER, BTC, BREAKOUT, Decimal("-2.00"), Decimal("6.00")),
        (1, TradingMode.PAPER, XAU, WITNESS, Decimal("12.50"), Decimal("5.00")),
        (0, TradingMode.PAPER, XAU, WITNESS, Decimal("-4.00"), Decimal("5.00")),
        (0, TradingMode.DEMO, BTC, BREAKOUT, Decimal("7.25"), Decimal("6.00")),
    ):
        store.apply_trade(
            day=NOW - timedelta(days=days_ago),
            mode=mode,
            market=market,
            ref=ref,
            pnl=pnl,
            risk_eur=risk,
            won=pnl > 0,
            at=NOW,
        )


def _chain(
    connection: Connection,
    version_id: int,
    market: str,
    direction: Direction,
    generated_at: datetime,
    closed_at: datetime | None,
    pnl: Decimal | None,
    risk: Decimal,
    mode: TradingMode,
    *,
    volume: Decimal = Decimal("0.02"),
    open_price: float = 2650.0,
    with_execution: bool = True,
) -> int:
    """One signal → one order → one position → (one trade). Returns the signal id."""
    signal = _signal(connection, version_id, market, direction, generated_at)
    order = _one(
        connection,
        OrderRow,
        idempotency_key=f"order:{signal}",
        signal_id=signal,
        symbol=market,
        direction=direction,
        volume=volume,
        requested_price=open_price,
        stop_loss=open_price - 10,
        take_profit=open_price + 10,
        mode=mode,
        state=OrderState.FILLED,
        broker_order_ticket=None,
        retcode=10009,
        broker_comment="done",
        created_at=generated_at,
        updated_at=generated_at,
    )
    position = _one(
        connection,
        PositionRow,
        broker_position_ticket=800000 + signal,
        order_id=order,
        symbol=market,
        direction=direction,
        volume=volume,
        open_price=open_price,
        stop_loss=open_price - 10,
        take_profit=open_price + 10,
        mode=mode,
        state=PositionState.OPEN if closed_at is None else PositionState.CLOSED,
        opened_at=generated_at,
        updated_at=closed_at or generated_at,
    )
    connection.execute(
        insert(RiskDecisionRow).values(
            signal_id=signal,
            outcome=RiskOutcome.AUTHORIZED,
            reason="dans les limites",
            checks={"daily_loss": True},
            volume=volume,
            risk_eur=risk,
            margin_eur=Decimal("12.00"),
            decided_at=generated_at,
        )
    )
    if with_execution:
        connection.execute(
            insert(ExecutionRow).values(
                order_id=order,
                broker_deal_ticket=900000 + signal,
                price=open_price,
                volume=volume,
                slippage=0.15,
                executed_at=generated_at + timedelta(seconds=2),
            )
        )
    if closed_at is not None and pnl is not None:
        connection.execute(
            insert(TradeRow).values(
                position_id=position,
                mode=mode,
                closed_at=closed_at,
                close_price=open_price + 5,
                pnl_eur=pnl,
                risk_eur=risk,
                exit_reason="take_profit",
            )
        )
    return signal


def _signal(
    connection: Connection, version_id: int, symbol: str, direction: Direction, at: datetime
) -> int:
    return _one(
        connection,
        SignalRow,
        idempotency_key=f"signal:{symbol}:{at.isoformat()}",
        strategy_version_id=version_id,
        symbol=symbol,
        timeframe=Timeframe.H1,
        direction=direction,
        mode=TradingMode.PAPER,
        observed_price=2650.0,
        entry_low=2648.0,
        entry_high=2652.0,
        stop_loss=2640.0,
        take_profits=[2660.0, 2670.0],
        reason="croisement de moyennes confirmé par le volume",
        indicators={"ema_fast": 2649.5, "ema_slow": 2645.1},
        generated_at=at,
        expires_at=at + timedelta(hours=4),
        state=SignalState.CLOSED,
    )


def _snapshots(connection: Connection) -> None:
    for moment, equity in (
        (NOW - timedelta(days=7), EQUITY_PEAK),
        (NOW - timedelta(days=1), Decimal("1020.00")),
        (NOW - timedelta(hours=1), EQUITY),
    ):
        connection.execute(
            insert(AccountSnapshotRow).values(equity=equity, balance=BALANCE, at=moment)
        )


def _events(connection: Connection) -> None:
    for kind, severity, detail, at in (
        ("broker_disconnected", Severity.WARNING, {"attempts": 3}, NOW - timedelta(hours=4)),
        ("clock_mismatch", Severity.CRITICAL, {"offset_seconds": 42}, NOW - timedelta(hours=2)),
        ("cycle", Severity.INFO, {"publications": 1}, NOW - timedelta(minutes=30)),
    ):
        connection.execute(
            insert(SystemEventRow).values(
                kind=kind, severity=severity, detail=detail, occurred_at=at
            )
        )
    # Telemetry of one complete hop sequence, so the latency table has a measured sample.
    for kind, offset in (
        (ExecutionEventKind.SIGNAL_GENERATED, 0.0),
        (ExecutionEventKind.ORDER_SENT, 1.0),
        (ExecutionEventKind.ORDER_ACCEPTED, 1.5),
        (ExecutionEventKind.FILLED, 2.0),
    ):
        connection.execute(
            insert(ExecutionEventRow).values(
                order_id=None,
                signal_id=None,
                symbol=XAU,
                kind=kind,
                detail={"hop": kind.value},
                occurred_at=NOW - timedelta(days=1, hours=4) + timedelta(seconds=offset),
            )
        )


def _halt(connection: Connection) -> None:
    connection.execute(
        insert(HaltCommandRow).values(
            scope="market:BTCUSD",
            action=HaltAction.HALT,
            close_positions=False,
            source=HaltSource.TELEGRAM,
            reason="volatilité excessive",
            actor="operator",
            occurred_at=NOW - timedelta(hours=3),
        )
    )


def _candles(connection: Connection) -> None:
    for symbol, opened_at in ((XAU, NOW - timedelta(hours=1)), (BTC, NOW - timedelta(hours=30))):
        connection.execute(
            insert(CandleRow).values(
                symbol=symbol,
                timeframe=Timeframe.H1,
                open_time=opened_at,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                source="mt5",
                ingested_at=opened_at + timedelta(minutes=1),
            )
        )


def _report(connection: Connection) -> int:
    return _one(
        connection,
        ReportRow,
        period="daily",
        window_start=NOW - timedelta(days=1),
        window_end=NOW,
        content=REPORT_CONTENT,
        generated_at=NOW - timedelta(hours=1),
        sent_at=None,
    )


def _registry(connection: Connection) -> None:
    connection.execute(
        insert(StrategyRegistryRow).values(
            market=XAU,
            ref=WITNESS,
            strategy_id="witness",
            version="1.0.0",
            status=StrategyStatus.LIVE,
            parent_ref=None,
            origin="human",
            parameters={"lookback": 20},
            results={"sharpe": 1.2},
            dataset_fingerprint="c" * 64,
            promotion_reason="validé sur 12 mois",
            created_at=NOW - timedelta(days=30),
            promoted_at=NOW - timedelta(days=10),
            updated_at=NOW - timedelta(days=10),
        )
    )
    connection.execute(
        insert(StrategyRegistryRow).values(
            market=BTC,
            ref=BREAKOUT,
            strategy_id="trend_breakout",
            version="1.0.0",
            status=StrategyStatus.PAPER,
            parent_ref=None,
            origin="ai",
            parameters={"threshold": 0.5},
            results=None,
            dataset_fingerprint=None,
            promotion_reason=None,
            created_at=NOW - timedelta(days=20),
            promoted_at=None,
            updated_at=NOW - timedelta(days=20),
        )
    )


def _research(connection: Connection) -> None:
    connection.execute(
        insert(BacktestRunRow).values(
            ref=WITNESS,
            market=XAU,
            dataset_id="xau-h1-2024",
            fingerprint="d" * 64,
            window_start=NOW - timedelta(days=365),
            window_end=NOW,
            metrics={"sharpe": 1.4, "profit_factor": 1.6},
            costs={"spread": 0.3, "commission": 0.0},
            report_path="docs/reports/witness.md",
            created_at=NOW - timedelta(days=12),
        )
    )
    for ref, market, stage, passed, detail, age in (
        (WITNESS, XAU, ValidationStage.WALK_FORWARD, True, {"folds": 6}, 11),
        (BREAKOUT, BTC, ValidationStage.MONTE_CARLO, False, {"p_value": 0.21}, 9),
    ):
        connection.execute(
            insert(ValidationRunRow).values(
                ref=ref,
                market=market,
                stage=stage,
                passed=passed,
                detail=detail,
                created_at=NOW - timedelta(days=age),
            )
        )


def _ai(connection: Connection, signal_id: int) -> None:
    analysis_id = _one(
        connection,
        AiAnalysisRow,
        kind=AnalysisKind.LOSS_ANALYSIS,
        market=XAU,
        ref=WITNESS,
        signal_id=signal_id,
        model="claude-sonnet-4-5",
        request={"question": "pourquoi cette perte"},
        response="Le régime de marché a changé.",
        findings={"regime": "range"},
        cost_eur=Decimal("0.012"),
        created_at=NOW - timedelta(hours=6),
    )
    connection.execute(
        insert(AiProposalRow).values(
            market=XAU,
            ref=WITNESS,
            analysis_id=analysis_id,
            hypothesis="Allonger la fenêtre de tendance réduit les faux signaux.",
            proposed_change={"lookback": 30},
            status=ProposalStatus.PROPOSED,
            decided_by=None,
            decision_reason=None,
            created_at=NOW - timedelta(hours=5),
            decided_at=None,
        )
    )


def _one(connection: Connection, table: Any, **values: Any) -> int:
    """Insert one row and return its primary key. ``table`` is free of naming collisions."""
    statement = insert(table).values(**values).returning(table.id)
    return int(connection.execute(statement).scalar_one())
