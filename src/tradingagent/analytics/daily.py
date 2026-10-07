"""The day's result: what the operator asks first, and the one number every surface shows.

The dashboard, the Telegram command and the terminal line all answer the same question —
"how much today?" — so they must read the same answer. Three implementations would drift,
and the one that drifts is always the one the operator happens to be looking at.

Two deliberate choices:

* **Realized only.** Open positions move with the market and would make the day's figure
  jump on every tick; the operator reads this number to know what happened, not to watch a
  P&L that can still be taken back. Unrealized equity is a different question, answered by
  the snapshot.
* **The balance comes from the last snapshot, never from the day's trades.** Summing the day
  onto a balance the account never held invents a figure. When no snapshot exists the
  balance is reported as unknown, which is honest, rather than as zero, which is a lie the
  operator would act on.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tradingagent.analytics.model import Performance, Trade
from tradingagent.analytics.performance import compute_performance
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.storage.account import AccountStore
from tradingagent.storage.models import (
    OrderRow,
    PositionRow,
    SignalRow,
    StrategyVersionRow,
    TradeRow,
)

# Every amount in this project is carried in euros: the columns are `pnl_eur` and `risk_eur`,
# and the demo account this runs against is denominated in EUR.
DEFAULT_CURRENCY = "EUR"


@dataclass(frozen=True)
class DailyResult:
    """One day of realized trading, plus the account state that goes with it."""

    day: datetime
    mode: TradingMode
    performance: Performance
    balance: Decimal | None = None
    equity: Decimal | None = None
    currency: str = DEFAULT_CURRENCY

    @property
    def net(self) -> Decimal:
        return self.performance.net_profit

    @property
    def trades(self) -> int:
        return self.performance.trades

    @property
    def amount(self) -> str:
        """The day's result, signed, without a currency."""
        return f"{self.net:+.2f}"

    @property
    def compact(self) -> str:
        """`+12.50 €` — the line the terminal prints on every close."""
        return f"{self.amount} {self.currency}"

    @property
    def balance_text(self) -> str:
        """The balance, or an explicit unknown rather than a convenient zero."""
        if self.balance is None:
            return "solde inconnu"
        return f"solde {self.balance:.2f} {self.currency}"

    def summary(self) -> str:
        """One line for the operator: the result, its shape, and the balance."""
        if self.trades == 0:
            return f"Aucun trade clôturé aujourd'hui · {self.balance_text}"
        shape = f"{self.performance.wins} gain(s)" if self.performance.wins else "aucun gain"
        shape += (
            f", {self.performance.losses} perte(s)" if self.performance.losses else ", aucune perte"
        )
        return f"{self.compact} sur {self.trades} trade(s) ({shape}) · {self.balance_text}"

    def detail(self) -> str:
        """The multi-line version, aligned, for the dashboard and the Telegram command."""
        rows: list[tuple[str, str]] = [
            ("Résultat", self.compact),
            ("Trades", f"{self.trades}"),
            ("Gains / pertes", f"{self.performance.wins} / {self.performance.losses}"),
        ]
        if self.performance.win_rate is not None:
            rows.append(("Taux de réussite", f"{self.performance.win_rate * 100:.0f} %"))
        if self.performance.best is not None:
            rows.append(("Meilleur trade", f"{self.performance.best:+.2f} {self.currency}"))
        if self.performance.worst is not None:
            rows.append(("Pire trade", f"{self.performance.worst:+.2f} {self.currency}"))
        if self.performance.max_drawdown:
            drawdown = f"{self.performance.max_drawdown:.2f} {self.currency}"
            rows.append(("Drawdown du jour", drawdown))
        rows.append(("Solde", self.balance_text))
        width = max(len(label) for label, _ in rows)
        return "\n".join(f"{label:<{width}}  {value}" for label, value in rows)


def day_bounds(day: datetime) -> tuple[datetime, datetime]:
    """The half-open UTC window of one day. Naive input is refused, not guessed."""
    if day.tzinfo is None:
        raise ValueError("a day needs a timezone: this project is UTC everywhere")
    start = day.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def closed_trades(
    engine: Engine,
    start: datetime,
    end: datetime,
    *,
    mode: TradingMode | None = None,
) -> list[Trade]:
    """Trades closed inside the window, optionally restricted to one execution mode.

    Paper and live results are never added together: a paper trade is a rehearsal, and
    folding it into the day's balance would report money that was never at risk.
    """
    statement = (
        select(TradeRow, PositionRow, SignalRow, StrategyVersionRow.ref)
        .join(PositionRow, TradeRow.position_id == PositionRow.id)
        .join(OrderRow, PositionRow.order_id == OrderRow.id)
        .join(SignalRow, OrderRow.signal_id == SignalRow.id)
        .join(StrategyVersionRow, SignalRow.strategy_version_id == StrategyVersionRow.id)
        .where(TradeRow.closed_at >= start, TradeRow.closed_at < end)
        .order_by(TradeRow.closed_at)
    )
    if mode is not None:
        statement = statement.where(TradeRow.mode == mode)
    with Session(engine) as session:
        rows = session.execute(statement).unique().all()
    return [
        Trade(
            symbol=position.symbol,
            strategy_ref=str(ref),
            direction=position.direction,
            timeframe=signal.timeframe,
            mode=trade.mode,
            opened_at=position.opened_at,
            closed_at=trade.closed_at,
            pnl_eur=Decimal(trade.pnl_eur),
            risk_eur=Decimal(trade.risk_eur),
            slippage=None,
            spread=None,
        )
        for trade, position, signal, ref in rows
    ]


def daily_result(
    engine: Engine,
    day: datetime,
    *,
    mode: TradingMode | None = None,
    currency: str = DEFAULT_CURRENCY,
) -> DailyResult:
    """Everything the operator's three surfaces need about one day."""
    start, end = day_bounds(day)
    trades = closed_trades(engine, start, end, mode=mode)
    snapshot = AccountStore(engine).latest_before(end)
    return DailyResult(
        day=start,
        mode=mode if mode is not None else TradingMode.SIGNAL,
        performance=compute_performance(trades),
        balance=snapshot.balance if snapshot is not None else None,
        equity=snapshot.equity if snapshot is not None else None,
        currency=currency,
    )


def timeframe_of(value: str) -> Timeframe:
    """Kept next to the query so a caller cannot invent a timeframe label."""
    return Timeframe(value)


__all__ = [
    "DEFAULT_CURRENCY",
    "DailyResult",
    "closed_trades",
    "daily_result",
    "day_bounds",
    "timeframe_of",
]
