"""Command routing and the first commands (TASK-020; more in TASK-022 and TASK-023).

Replies are plain text: no Markdown, so nothing coming from data can break the formatting.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from tradingagent.core.mode import TradingMode
from tradingagent.storage.halts import HaltStore


@dataclass(frozen=True)
class CommandRequest:
    user_id: int
    command: str
    args: tuple[str, ...]
    at: datetime


Handler = Callable[[CommandRequest], Awaitable[str]]

# The mode must never be ambiguous: real money is spelled out and flagged.
MODE_LABELS = {
    TradingMode.OBSERVATION: "OBSERVATION (aucun signal envoyé, aucun ordre)",
    TradingMode.SIGNAL: "SIGNAL (signaux seulement, aucun ordre)",
    TradingMode.PAPER: "PAPER (ordres simulés, aucun argent engagé)",
    TradingMode.DEMO: "DÉMO (ordres sur le compte de démonstration)",
    TradingMode.LIVE: "⚠️ RÉEL ⚠️ (ordres avec de l'argent réel)",
}


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
            return f"Commande inconnue : /{request.command}. Tape /help pour la liste."
        return await entry[1](request)

    async def _help(self, _: CommandRequest) -> str:
        lines = ["Commandes disponibles :"]
        lines += [
            f"/{name} : {description}" for name, (description, _h) in sorted(self._handlers.items())
        ]
        return "\n".join(lines)


def status_handler(halts: HaltStore, mode: TradingMode) -> Handler:
    async def status(_: CommandRequest) -> str:
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
            lines.append("Stratégies en quarantaine :")
            lines += quarantine_lines
        return "\n".join(lines)

    return status
