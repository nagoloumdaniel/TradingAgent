"""Everything between a received message and the reply, without Telegram itself.

A command runs only after it has been recorded: if the audit log is unavailable, nothing
runs. Strangers, group chats and flooded identifiers get no reply at all.

A button click is not a second road: `handle_callback` turns the payload back into the
command it names and hands it to `handle`, so a click passes the same gate, writes the
same audit row before execution, and reaches the same handler as the typed command.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from tradingagent.notify.access import AccessGate, Verdict
from tradingagent.notify.commands import CommandRequest, CommandRouter
from tradingagent.notify.replies import Reply, as_reply, decode_callback, is_fresh
from tradingagent.storage.audit import AuditStore

log = logging.getLogger(__name__)

RATE_LIMITED_REPLY = "Trop de commandes en peu de temps : réessaie dans quelques minutes."
NOT_A_COMMAND_REPLY = "Je ne comprends que les commandes.\nTape /help pour voir la liste."
STALE_CALLBACK_REPLY = (
    "Ce bouton vient d'un message trop ancien : rien n'a été exécuté.\nVoici les choix à jour."
)


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

    @property
    def router(self) -> CommandRouter:
        """The single palette: the menu Telegram shows is derived from it, never copied."""
        return self._router

    async def handle(self, user_id: int, private_chat: bool, text: str) -> Reply | None:
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
                return Reply(RATE_LIMITED_REPLY)
            return None

        if parsed is None:
            return Reply(NOT_A_COMMAND_REPLY)
        try:
            await asyncio.to_thread(
                self._audit.record, actor, "command", {"command": command, "args": list(args)}, at
            )
        except Exception:
            log.exception("audit log unavailable, /%s not executed", command)
            return Reply(
                f"Commande /{command} non exécutée : le journal est indisponible.\n"
                f"Rien n'a été lancé. Réessaie dans un instant."
            )
        try:
            return as_reply(await self._router.dispatch(CommandRequest(user_id, command, args, at)))
        except Exception:
            log.exception("/%s failed", command)
            return Reply(
                f"La commande /{command} a échoué sur une erreur interne.\n"
                f"Tape /status pour vérifier l'état de l'agent."
            )

    async def handle_callback(self, user_id: int, private_chat: bool, data: str) -> Reply | None:
        """A click on an inline button, replayed as the command the button names.

        The payload is untrusted: it is decoded strictly, and whatever it names is then
        replayed through `handle`. A payload that is not one of ours — forged by hand,
        truncated, from another bot — only re-opens /help. One that is too old re-opens
        its own menu instead of acting, so an outdated message can never decide anything.
        """
        callback = decode_callback(data)
        at = self._now()
        if callback is None:
            return await self.handle(user_id, private_chat, "/help")
        if not is_fresh(callback, at):
            reopened = await self.handle(user_id, private_chat, f"/{callback.command}")
            if reopened is None:
                return None
            return Reply(f"{STALE_CALLBACK_REPLY}\n\n{reopened}", reopened.keyboard)
        return await self.handle(
            user_id, private_chat, " ".join((f"/{callback.command}", *callback.args))
        )

    async def _record_safely(
        self, actor: str, action: str, detail: dict[str, object], at: datetime
    ) -> None:
        try:
            await asyncio.to_thread(self._audit.record, actor, action, detail, at)
        except Exception:
            log.exception("could not record a refused command")
