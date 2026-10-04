"""Everything between a received message and the reply, without Telegram itself.

A command runs only after it has been recorded: if the audit log is unavailable, nothing
runs. Strangers, group chats and flooded identifiers get no reply at all.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from tradingagent.notify.access import AccessGate, Verdict
from tradingagent.notify.commands import CommandRequest, CommandRouter
from tradingagent.storage.audit import AuditStore

log = logging.getLogger(__name__)

RATE_LIMITED_REPLY = "Trop de commandes en peu de temps : réessaie dans quelques minutes."
NOT_A_COMMAND_REPLY = "Je ne comprends que les commandes. Tape /help pour la liste."


def _utc_now() -> datetime:
    return datetime.now(UTC)


def parse_command(text: str) -> tuple[str, tuple[str, ...]] | None:
    words = text.strip().split()
    if not words or not words[0].startswith("/"):
        return None
    name = words[0][1:].split("@", 1)[0].lower()
    return (name, tuple(words[1:])) if name else None


class CommandService:
    def __init__(
        self,
        gate: AccessGate,
        router: CommandRouter,
        audit: AuditStore,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._gate = gate
        self._router = router
        self._audit = audit
        self._now = now

    async def handle(self, user_id: int, private_chat: bool, text: str) -> str | None:
        at = self._now()
        parsed = parse_command(text)
        command, args = parsed if parsed is not None else ("", ())
        decision = self._gate.check(user_id, private_chat, at)
        actor = f"telegram:{user_id}"

        if decision.verdict is not Verdict.ALLOWED:
            if decision.record:
                await self._record_safely(
                    actor,
                    "command_refused",
                    {"command": command, "verdict": str(decision.verdict)},
                    at,
                )
            log.warning("telegram %s refused for %s", decision.verdict, user_id)
            # Only the operator may learn about the limit; anyone else gets silence.
            if decision.newly_limited and self._gate.is_operator(user_id) and private_chat:
                return RATE_LIMITED_REPLY
            return None

        if parsed is None:
            return NOT_A_COMMAND_REPLY
        try:
            await asyncio.to_thread(
                self._audit.record, actor, "command", {"command": command, "args": list(args)}, at
            )
        except Exception:
            log.exception("audit log unavailable, /%s not executed", command)
            return "Le journal des commandes est indisponible : commande non exécutée."
        try:
            return await self._router.dispatch(CommandRequest(user_id, command, args, at))
        except Exception:
            log.exception("/%s failed", command)
            return f"La commande /{command} a rencontré une erreur interne. Voir les journaux."

    async def _record_safely(
        self, actor: str, action: str, detail: dict[str, object], at: datetime
    ) -> None:
        try:
            await asyncio.to_thread(self._audit.record, actor, action, detail, at)
        except Exception:
            log.exception("could not record a refused command")
