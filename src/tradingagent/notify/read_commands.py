"""The operator's read commands: /markets, /signals, /positions, /performance (TASK-022),
plus the per-market control centre: /marche, /propositions, /portes.

Each factory closes over one database handle and returns a plain handler for the router.
Every database call runs in a thread, like the rest of the bot; the answers are plain text
with UTC timestamps, and say so when there is nothing to report.
"""

import asyncio
import textwrap
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta

from sqlalchemy import Engine

from tradingagent.ai.lab_store import LabStore, StoredProposal
from tradingagent.core.halt import market_scope
from tradingagent.core.states import ProposalStatus
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.market_calendar import MarketCalendar, SlotStatus
from tradingagent.notify.commands import (
    MODE_SHORT_LABELS,
    CommandRequest,
    Handler,
    age,
    market_line,
)
from tradingagent.notify.signal_template import DIRECTION_LABELS
from tradingagent.registry.gates import PROMOTION_GATES, missing_gates
from tradingagent.registry.store import StrategyRegistry
from tradingagent.reporting.generator import ReportGenerator
from tradingagent.reporting.schedule import Period, window_containing
from tradingagent.storage.account import AccountStore, ReportData
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.halts import HaltStore
from tradingagent.storage.performance import PerformanceReader
from tradingagent.storage.positions import PositionReader
from tradingagent.storage.signals import RecentSignal, read_last_signal, read_recent_signals

REPORT_PERIODS = {
    "daily": Period.DAILY,
    "quotidien": Period.DAILY,
    "weekly": Period.WEEKLY,
    "hebdomadaire": Period.WEEKLY,
    "monthly": Period.MONTHLY,
    "mensuel": Period.MONTHLY,
}

# One line of a read answer never exceeds this, so it stays readable on a phone.
LINE_WIDTH = 80
LABEL_WIDTH = 16

CALENDAR_LABELS = {
    SlotStatus.OPEN: "ouvert",
    SlotStatus.CLOSED: "fermé",
    SlotStatus.UNCERTAIN: "incertain (horaires appris incomplets)",
}

PROPOSAL_LABELS = {
    ProposalStatus.PROPOSED: "proposée",
    ProposalStatus.VALIDATING: "en validation",
    ProposalStatus.REJECTED: "refusée",
    ProposalStatus.PROMOTED: "acceptée (promue)",
}


def _clip(text: str, width: int) -> str:
    """One line, never longer than `width`. Long data is cut, never spilled.

    The leading indent is kept: a value that is one line of a block stays inside its block,
    and collapsing its whitespace would silently pull it back to the margin.
    """
    indent = text[: len(text) - len(text.lstrip())]
    flat = indent + " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 3].rstrip() + "..."


def _field(label: str, value: str) -> str:
    return f"  {label:<{LABEL_WIDTH}} : {value}"


def _known_symbols(markets: Sequence[tuple[str, bool]]) -> list[str]:
    return [symbol for symbol, _enabled in markets]


def _unknown_market(symbol: str, markets: Sequence[tuple[str, bool]]) -> str | None:
    """The reply for a market the operator does not follow, or None when it is known."""
    known = _known_symbols(markets)
    if symbol in known:
        return None
    if not known:
        return "Aucun marché configuré.\nRien à afficher."
    return f"Marché inconnu : {symbol}\nMarchés suivis : {', '.join(known)}."


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


# --- The per-market control centre -------------------------------------------
#
# One market is one market: every line below is read for the requested symbol, never for
# "the markets" at large. XAUUSD and BTCUSD share no state here, so a halt or a position on
# one can never be displayed as if it belonged to the other.


def market_handler(
    markets: Sequence[tuple[str, bool]],
    candles: CandleStore,
    halts: HaltStore,
    engine: Engine,
    calendar_for: Callable[[str], MarketCalendar | None] | None = None,
    timeframe: Timeframe = Timeframe.M15,
) -> Handler:
    """`/marche <SYMBOLE>`: everything the operator needs about one market, in four blocks."""

    async def read_market(request: CommandRequest) -> str:
        if not markets:
            return "Aucun marché configuré.\nRien à afficher."
        if not request.args:
            known = ", ".join(_known_symbols(markets))
            return f"Quel marché ? Exemple : /marche XAUUSD\nMarchés suivis : {known}."
        symbol = request.args[0].strip().upper()
        unknown = _unknown_market(symbol, markets)
        if unknown is not None:
            return unknown
        enabled = dict(markets)[symbol]

        halt = await asyncio.to_thread(halts.status)
        halted_here = await asyncio.to_thread(halts.is_halted, market_scope(symbol))
        try:
            quarantined = [
                ref
                for ref, pair_symbol in await asyncio.to_thread(halts.halted_pairs)
                if pair_symbol == symbol
            ]
        except Exception:
            quarantined = ["illisibles"]
        positions = [
            position
            for position in await asyncio.to_thread(PositionReader(engine).open_positions)
            if position.symbol == symbol
        ]
        last_signal = await asyncio.to_thread(read_last_signal, engine, symbol)
        last_open = await asyncio.to_thread(candles.last_open_time, symbol, timeframe)
        calendar = calendar_for(symbol) if calendar_for is not None else None

        configuration = [
            _field("Suivi", "activé" if enabled else "désactivé"),
            _field(
                "Calendrier",
                CALENDAR_LABELS[calendar.status_at(request.at)]
                if calendar is not None
                else "inconnu (pas encore appris)",
            ),
            _field("Dernière bougie", _last_candle(last_open, timeframe, request.at)),
        ]

        if halt.halted:
            orders = "suspendus (arrêt global)"
        elif halted_here:
            orders = "suspendus sur ce marché"
        else:
            orders = "autorisés"
        control = [
            _field("Nouveaux ordres", orders),
            _field("Quarantaine", ", ".join(sorted(quarantined)) if quarantined else "aucune"),
            _field("Arrêter", f"/disable {symbol}"),
            _field("Reprendre", f"/enable {symbol}"),
        ]

        if positions:
            held = [
                _clip(
                    f"  {DIRECTION_LABELS[position.direction]} {position.volume} lot "
                    f"@ {position.open_price:.2f} ({MODE_SHORT_LABELS[position.mode]}), "
                    f"ouvert le {position.opened_at:%Y-%m-%d %H:%M} UTC",
                    LINE_WIDTH,
                )
                for position in positions
            ]
        else:
            held = ["  Aucune position ouverte sur ce marché."]

        activity = [
            _field("Dernier signal", _last_signal(last_signal)),
        ]

        blocks = [
            f"{symbol} · état du marché",
            _block("Configuration", configuration),
            _block("Contrôle", control),
            _block("Position", held),
            _block("Dernière activité", activity),
        ]
        return "\n\n".join(blocks)

    return read_market


def proposals_handler(
    engine: Engine,
    limit: int = 4,
    markets: Sequence[tuple[str, bool]] = (),
) -> Handler:
    """`/propositions [SYMBOLE]`: what the AI proposed, on what evidence, and its state.

    The proposals are read as persisted (`ai_proposals`), never recomputed: a proposal is
    the researcher's hypothesis, and its decision belongs to the validation system.
    """

    async def read_proposals(request: CommandRequest) -> str:
        symbol: str | None = None
        if request.args:
            symbol = request.args[0].strip().upper()
            unknown = _unknown_market(symbol, markets)
            if unknown is not None:
                return unknown
        store = LabStore(engine)
        proposals = await asyncio.to_thread(store.recent_proposals, market=symbol, limit=limit)
        title = "Propositions de l'IA" + (f" · {symbol}" if symbol else "")
        if not proposals:
            where = f"pour {symbol}" if symbol else "enregistrée"
            return f"Aucune proposition {where}.\nL'IA n'a rien proposé pour l'instant."
        blocks = [title, *[_proposal_block(proposal) for proposal in proposals]]
        return "\n\n".join(blocks)

    return read_proposals


def gates_handler(engine: Engine, markets: Sequence[tuple[str, bool]] = ()) -> Handler:
    """`/portes <SYMBOLE> [REF]`: which promotion gates are still missing, and nothing else.

    The gates come from the stored validations (`validation_runs`), through the same pure
    rule the registry refuses a promotion with: the answer here is the answer there.
    """

    async def read_gates(request: CommandRequest) -> str:
        if not request.args:
            return (
                "Quel marché ? Exemple : /portes XAUUSD\n"
                "Pour une version précise : /portes XAUUSD witness@1.1.0"
            )
        symbol = request.args[0].strip().upper()
        unknown = _unknown_market(symbol, markets)
        if unknown is not None:
            return unknown
        wanted = request.args[1].strip() if len(request.args) > 1 else None
        registry = StrategyRegistry(engine)
        rows = await asyncio.to_thread(registry.list_market, symbol)
        if wanted is not None:
            rows = [row for row in rows if row.ref == wanted]
            if not rows:
                return f"Version inconnue : {wanted} pour {symbol}."
        if not rows:
            return f"Aucune stratégie enregistrée pour {symbol}."

        blocks = [f"Portes de promotion · {symbol}"]
        for row in rows:
            passed = await asyncio.to_thread(registry.passed_stages, row.ref, symbol)
            missing = missing_gates(passed)
            total = len(PROMOTION_GATES)
            lines = [
                f"  {_clip(row.ref, 44)} · {row.status.value} · {total - len(missing)}/{total}"
            ]
            if missing:
                lines.append("    Manquantes :")
                lines += _wrap_names(stage.value for stage in missing)
            else:
                lines.append("    Toutes les portes sont franchies : promotion possible.")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    return read_gates


def _block(title: str, lines: Sequence[str]) -> str:
    return "\n".join([title, *lines])


def _wrap_names(names: Iterable[str]) -> list[str]:
    """The missing gates, wrapped at the line width so a nine-gate answer still fits."""
    return textwrap.wrap(
        ", ".join(names),
        width=LINE_WIDTH,
        initial_indent="      ",
        subsequent_indent="      ",
        break_long_words=False,
    )


def _last_candle(last_open: datetime | None, timeframe: Timeframe, at: datetime) -> str:
    if last_open is None:
        return "aucune bougie stockée"
    closed_at = last_open + timedelta(seconds=timeframe.seconds)
    return f"{last_open:%Y-%m-%d %H:%M} UTC (clôturée {age(at, closed_at)})"


def _last_signal(signal: RecentSignal | None) -> str:
    if signal is None:
        return "aucun signal enregistré"
    return (
        f"{signal.generated_at:%Y-%m-%d %H:%M} UTC "
        f"({DIRECTION_LABELS[signal.direction]}, {signal.state.value})"
    )


def _proposal_block(proposal: StoredProposal) -> str:
    created = f"{proposal.created_at:%Y-%m-%d %H:%M} UTC"
    head = f"  #{proposal.id} {proposal.market} · {PROPOSAL_LABELS[proposal.status]} · {created}"
    evidence = [part for part in (proposal.ref, _analysis(proposal.analysis_id)) if part]
    if proposal.decided_by or proposal.decision_reason:
        decision = _clip(
            " · ".join(part for part in (proposal.decided_by, proposal.decision_reason) if part),
            LINE_WIDTH - 16,
        )
    else:
        decision = "en attente de décision"
    return "\n".join(
        [
            _clip(head, LINE_WIDTH),
            f"    {'Hypothèse':<9} : {_clip(proposal.hypothesis, LINE_WIDTH - 16)}",
            f"    {'Preuve':<9} : {_clip(' · '.join(evidence), LINE_WIDTH - 16)}"
            if evidence
            else f"    {'Preuve':<9} : aucune preuve enregistrée",
            f"    {'Décision':<9} : {decision}",
        ]
    )


def _analysis(analysis_id: int | None) -> str | None:
    return f"analyse #{analysis_id}" if analysis_id is not None else None
