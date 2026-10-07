"""The operator's read commands: /markets, /signals, /positions, /performance (TASK-022).

Each factory closes over one database handle and returns a plain handler for the router.
Every database call runs in a thread, like the rest of the bot; the answers are plain text
with UTC timestamps, and say so when there is nothing to report.
"""

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime

from sqlalchemy import Engine

from tradingagent.core.timeframe import Timeframe
from tradingagent.notify.commands import CommandRequest, Handler, market_line
from tradingagent.notify.signal_template import DIRECTION_LABELS
from tradingagent.reporting.generator import ReportGenerator
from tradingagent.reporting.schedule import Period, window_containing
from tradingagent.storage.account import AccountStore, ReportData
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.performance import PerformanceReader
from tradingagent.storage.positions import PositionReader
from tradingagent.storage.signals import read_recent_signals

REPORT_PERIODS = {
    "daily": Period.DAILY,
    "quotidien": Period.DAILY,
    "weekly": Period.WEEKLY,
    "hebdomadaire": Period.WEEKLY,
    "monthly": Period.MONTHLY,
    "mensuel": Period.MONTHLY,
}


def markets_handler(
    markets: Sequence[tuple[str, bool]],
    candles: CandleStore,
    timeframe: Timeframe = Timeframe.M15,
) -> Handler:
    async def read_markets(request: CommandRequest) -> str:
        lines: list[str] = []
        for symbol, enabled in markets:
            lines.append(await market_line(symbol, enabled, candles, timeframe, request.at))
        return "\n".join(lines) if lines else "Aucun marché configuré."

    return read_markets


def signals_handler(engine: Engine, limit: int = 10) -> Handler:
    async def signals(_: CommandRequest) -> str:
        recent = await asyncio.to_thread(read_recent_signals, engine, limit)
        if not recent:
            return "Aucun signal enregistré."
        lines = [
            f"{signal.generated_at:%Y-%m-%d %H:%M} UTC · {signal.symbol} "
            f"{signal.timeframe.value} {DIRECTION_LABELS[signal.direction]} · "
            f"{signal.state.value}"
            for signal in recent
        ]
        return "\n".join(lines)

    return signals


def positions_handler(engine: Engine) -> Handler:
    async def positions(_: CommandRequest) -> str:
        opened = await asyncio.to_thread(PositionReader(engine).open_positions)
        if not opened:
            return "Aucune position ouverte."
        lines = [
            f"{position.symbol} {DIRECTION_LABELS[position.direction]} "
            f"{position.volume} @ {position.open_price:.2f} ({position.mode.value}), "
            f"ouvert le {position.opened_at:%Y-%m-%d %H:%M} UTC"
            for position in opened
        ]
        return "\n".join(lines)

    return positions


def performance_handler(engine: Engine) -> Handler:
    async def performance(_: CommandRequest) -> str:
        summary = await asyncio.to_thread(PerformanceReader(engine).summary)
        if summary.trades == 0:
            return "Aucun trade clôturé."
        rate = summary.win_rate
        rate_text = f"{rate * 100:.1f} %" if rate is not None else "n/a"
        lines = [
            f"Trades clôturés : {summary.trades} · gagnants : {summary.wins} "
            f"({rate_text}) · PnL total : {summary.total_pnl_eur:+.2f} €"
        ]
        lines += [
            f"{entry.mode.value} : {entry.trades} trade(s), PnL {entry.total_pnl_eur:+.2f} €"
            for entry in summary.by_mode
        ]
        return "\n".join(lines)

    return performance


def report_handler(engine: Engine, now: Callable[[], datetime]) -> Handler:
    """`/report daily|weekly|monthly`: the same numbers the scheduled report sends,
    assembled from the database alone (F-022)."""

    async def report(request: CommandRequest) -> str:
        name = request.args[0].lower() if request.args else "daily"
        period = REPORT_PERIODS.get(name)
        if period is None:
            return "Usage : /report daily|weekly|monthly"
        window = window_containing(period, now())
        generator = ReportGenerator(ReportData(engine), AccountStore(engine))
        return await asyncio.to_thread(generator.build, window)

    return report
