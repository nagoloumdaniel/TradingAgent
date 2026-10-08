"""Command routing and the first commands (TASK-020; more in TASK-022 and TASK-023).

Replies are plain text: no Markdown, so nothing coming from data can break the formatting.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.halts import HaltStore


@dataclass(frozen=True)
class CommandRequest:
    user_id: int
    command: str
    args: tuple[str, ...]
    at: datetime


Handler = Callable[[CommandRequest], Awaitable[str]]

# The mode must never be ambiguous: real money is spelled out and flagged. This long form is
# for the answers to a command, where the operator asked for the detail.
MODE_LABELS = {
    TradingMode.OBSERVATION: "OBSERVATION (aucun signal envoyé, aucun ordre)",
    TradingMode.SIGNAL: "SIGNAL (signaux seulement, aucun ordre)",
    TradingMode.PAPER: "PAPER (ordres simulés, aucun argent engagé)",
    TradingMode.DEMO: "DÉMO (ordres sur le compte de démonstration)",
    TradingMode.LIVE: "⚠️ RÉEL ⚠️ (ordres avec de l'argent réel)",
}

# One word, for the messages the agent pushes without being asked: a signal is already
# three lines of numbers, the parenthetical does not survive the trip to a phone.
MODE_SHORT_LABELS = {
    TradingMode.OBSERVATION: "OBSERVATION",
    TradingMode.SIGNAL: "SIGNAL",
    TradingMode.PAPER: "PAPER",
    TradingMode.DEMO: "DÉMO",
    TradingMode.LIVE: "RÉEL",
}


class Usage(StrEnum):
    """How /help groups the palette: read, choose, act. The order is the reading order."""

    CONSULTER = "CONSULTER"
    CONTROLER = "CONTRÔLER"
    AGIR = "AGIR"


USAGE_TAGLINES = {
    Usage.CONSULTER: "lire l'état, rien ne change",
    Usage.CONTROLER: "choisir, avec les conséquences annoncées",
    Usage.AGIR: "arrêter ou reprendre l'agent",
}

# One line per command: what to type, then what it is for, in one sentence. The table is
# keyed by command name so a caller that registers three positional arguments — the
# composition root, the tests — documents nothing twice. A command with no entry here
# still appears in /help, under AUTRES, with the description it was registered with.
COMMAND_HELP: dict[str, tuple[Usage, str, str]] = {
    "help": (Usage.CONSULTER, "/help", "cette page, la palette groupée par usage"),
    "status": (Usage.CONSULTER, "/status", "mode, arrêt actif, quarantaines, marchés"),
    "markets": (Usage.CONSULTER, "/markets", "marchés suivis et fraîcheur des bougies"),
    "marche": (Usage.CONSULTER, "/marche SYM", "un marché : suivi, position, haltes, calendrier"),
    "signals": (Usage.CONSULTER, "/signals", "les derniers signaux produits"),
    "positions": (Usage.CONSULTER, "/positions", "les positions ouvertes et leur entrée"),
    "performance": (Usage.CONSULTER, "/performance", "les trades clôturés, gagnants et PnL"),
    "report": (Usage.CONSULTER, "/report PERIODE", "le rapport quotidien, hebdo ou mensuel"),
    "propositions": (
        Usage.CONSULTER,
        "/propositions [SYM]",
        "ce que l'IA propose et la décision prise",
    ),
    "portes": (Usage.CONSULTER, "/portes SYM [REF]", "les portes de promotion manquantes"),
    "mode": (Usage.CONTROLER, "/mode", "voir et changer le mode, conséquences comprises"),
    "disable": (Usage.CONTROLER, "/disable SYM", "couper un marché précis"),
    "enable": (Usage.CONTROLER, "/enable SYM", "rouvrir un marché précis"),
    "pause": (Usage.AGIR, "/pause", "suspendre les nouveaux ordres, garder les positions"),
    "resume": (Usage.AGIR, "/resume", "reprendre après une pause ou un arrêt d'urgence"),
    "close_all": (Usage.AGIR, "/close_all", "fermer toutes les positions au marché"),
    "emergency_stop": (Usage.AGIR, "/emergency_stop", "tout arrêter, sans clôturer les positions"),
}

HELP_HEADER = "🧭 Commandes du bot, par usage"
HELP_FOOTER = (
    "Les commandes qui changent l'état demandent « confirmer ».\n"
    "Tape /mode ou /pause : les choix s'affichent en boutons."
)
HELP_OTHER_GROUP = "AUTRES"
HELP_OTHER_TAGLINE = "sans fiche détaillée"

# The width of the column that holds what to type, and the width of one line on a phone.
HELP_COLUMN = 20
LINE_WIDTH = 80


class CommandRouter:
    def __init__(self) -> None:
        self._handlers: dict[str, tuple[str, Handler]] = {}
        self.register("help", "liste des commandes", self._help)

    def register(self, name: str, description: str, handler: Handler) -> None:
        if name in self._handlers and name != "help":
            raise ValueError(f"command /{name} registered twice")
        self._handlers[name] = (description, handler)

    def knows(self, name: str) -> bool:
        return name in self._handlers

    async def dispatch(self, request: CommandRequest) -> str:
        entry = self._handlers.get(request.command)
        if entry is None:
            return f"Commande inconnue : /{request.command}\nTape /help pour voir la liste."
        return await entry[1](request)

    async def _help(self, _: CommandRequest) -> str:
        """The palette, grouped by usage: the operator reads three short sections."""
        blocks: list[str] = [HELP_HEADER]
        for usage in Usage:
            names = sorted(
                name
                for name in self._handlers
                if name in COMMAND_HELP and COMMAND_HELP[name][0] is usage
            )
            if not names:
                continue
            heading = f"▸ {usage.value} — {USAGE_TAGLINES[usage]}"
            blocks.append("\n".join([heading, *(self._help_line(name) for name in names)]))
        others = sorted(name for name in self._handlers if name not in COMMAND_HELP)
        if others:
            heading = f"▸ {HELP_OTHER_GROUP} — {HELP_OTHER_TAGLINE}"
            blocks.append("\n".join([heading, *(self._help_line(name) for name in others)]))
        blocks.append(HELP_FOOTER)
        return "\n\n".join(blocks)

    def _help_line(self, name: str) -> str:
        documented = COMMAND_HELP.get(name)
        if documented is None:
            signature, purpose = f"/{name}", self._handlers[name][0]
        else:
            signature, purpose = documented[1], documented[2]
        line = f"  {signature:<{HELP_COLUMN}} {' '.join(purpose.split())}".rstrip()
        return line if len(line) <= LINE_WIDTH else line[: LINE_WIDTH - 3].rstrip() + "..."


def age(at: datetime, since: datetime) -> str:
    """How long ago `since` was, in minutes: shared by every read answer."""
    minutes = int((at - since).total_seconds() // 60)
    return "à l'instant" if minutes <= 0 else f"il y a {minutes} min"


async def market_line(
    symbol: str,
    enabled: bool,
    candles: CandleStore,
    timeframe: Timeframe,
    at: datetime,
) -> str:
    """One line of /markets and of /status: configured state plus data freshness.

    The symbol is padded so that a column of markets reads as a column, not as prose.
    """
    head = f"  {symbol:<10} : "
    state = "activé" if enabled else "désactivé"
    last_open = await asyncio.to_thread(candles.last_open_time, symbol, timeframe)
    if last_open is None:
        return f"{head}{state}, aucune bougie stockée"
    closed_at = last_open + timedelta(seconds=timeframe.seconds)
    return (
        f"{head}{state}, dernière bougie {last_open:%Y-%m-%d %H:%M} UTC "
        f"(clôturée {age(at, closed_at)})"
    )


def status_handler(
    halts: HaltStore,
    mode: TradingMode,
    markets: Sequence[tuple[str, bool]] = (),
    candles: CandleStore | None = None,
    timeframe: Timeframe = Timeframe.M15,
) -> Handler:
    async def status(request: CommandRequest) -> str:
        halt = await asyncio.to_thread(halts.status)
        try:
            pairs = await asyncio.to_thread(halts.halted_pairs)
            quarantine_lines = [f"  {ref} sur {symbol}" for ref, symbol in sorted(pairs)]
        except Exception:
            quarantine_lines = ["  illisibles"]
        lines = [f"Mode : {MODE_LABELS[mode]}"]
        if halt.halted:
            lines.append("État : ARRÊT ACTIF, aucun nouvel ordre")
            if halt.close_positions:
                lines.append("Fermeture des positions ouvertes demandée")
            lines += [f"  {reason}" for reason in halt.reasons]
        else:
            lines.append("État : en marche, aucun arrêt actif")
        if quarantine_lines:
            lines += ["", "Stratégies en quarantaine :", *quarantine_lines]
        if markets and candles is not None:
            lines += ["", "Marchés :"]
            for symbol, enabled in markets:
                lines.append(await market_line(symbol, enabled, candles, timeframe, request.at))
        return "\n".join(lines)

    return status
