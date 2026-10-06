"""The operator's sensitive commands, each behind an explicit confirmation (TASK-023).

The confirmation convention: a bare command answers with the exact effect it would have
and asks for `/command confirmer`. Nothing is persisted until that word comes back, and
every execution is journalled by CommandService with its author.

Semantics (F-019, RM-000, RM-015): a pause and an emergency stop forbid new orders and
keep open positions; only /close_all asks for positions to be closed; the real-money mode
is unreachable from Telegram alone.
"""

import asyncio

from sqlalchemy import Engine

from tradingagent.core.halt import GLOBAL, market_scope
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import HaltAction, HaltSource, Severity
from tradingagent.notify.commands import CommandRequest, Handler
from tradingagent.storage.events import SystemEventStore
from tradingagent.storage.halts import HaltCommand, HaltStore

CONFIRM = "confirmer"
TELEGRAM = HaltSource.TELEGRAM


def _actor(user_id: int) -> str:
    return f"telegram:{user_id}"


def _unconfirmed(command: str, effect: str) -> str:
    return f"{effect}\nPour confirmer : /{command} {CONFIRM}"


def pause_handler(halts: HaltStore) -> Handler:
    effect = (
        "Suspend les nouveaux ordres sur tous les marchés. Les positions ouvertes sont conservées."
    )

    async def pause(request: CommandRequest) -> str:
        if not request.args or request.args[0].lower() != CONFIRM:
            return _unconfirmed("pause", effect)
        await asyncio.to_thread(
            halts.issue,
            HaltCommand(
                GLOBAL,
                HaltAction.HALT,
                TELEGRAM,
                "orders suspended by the operator",
                _actor(request.user_id),
                request.at,
            ),
        )
        return f"{effect} C'est fait. /resume pour reprendre."

    return pause


def resume_handler(halts: HaltStore) -> Handler:
    effect = "Reprend l'émission d'ordres après une suspension ou un arrêt d'urgence."

    async def resume(request: CommandRequest) -> str:
        if not request.args or request.args[0].lower() != CONFIRM:
            return _unconfirmed("resume", effect)
        await asyncio.to_thread(
            halts.issue,
            HaltCommand(
                GLOBAL,
                HaltAction.RESUME,
                TELEGRAM,
                "orders resumed by the operator",
                _actor(request.user_id),
                request.at,
            ),
        )
        return f"{effect} C'est fait."

    return resume


def close_all_handler(halts: HaltStore) -> Handler:
    effect = (
        "FERME toutes les positions ouvertes au marché, puis suspend les nouveaux ordres. "
        "Les positions seront réellement clôturées."
    )

    async def close_all(request: CommandRequest) -> str:
        if not request.args or request.args[0].lower() != CONFIRM:
            return _unconfirmed("close_all", effect)
        await asyncio.to_thread(
            halts.issue,
            HaltCommand(
                GLOBAL,
                HaltAction.HALT,
                TELEGRAM,
                "closing every open position, operator command",
                _actor(request.user_id),
                request.at,
                True,
            ),
        )
        return "Clôture demandée : chaque position ouverte sera fermée au marché."

    return close_all


def emergency_stop_handler(halts: HaltStore) -> Handler:
    effect = (
        "ARRÊT D'URGENCE : plus aucun nouvel ordre, quelle que soit la source. "
        "Les positions ouvertes ne sont pas clôturées automatiquement (RM-015)."
    )

    async def emergency_stop(request: CommandRequest) -> str:
        if not request.args or request.args[0].lower() != CONFIRM:
            return _unconfirmed("emergency_stop", effect)
        await asyncio.to_thread(
            halts.issue,
            HaltCommand(
                GLOBAL,
                HaltAction.HALT,
                TELEGRAM,
                "EMERGENCY STOP issued by the operator",
                _actor(request.user_id),
                request.at,
            ),
        )
        return "Arrêt d'urgence actif. /resume pour le lever après contrôle du compte."

    return emergency_stop


_SYMBOL_FORBIDDEN = set(" \t:,'\"")


def _validate_symbol(args: tuple[str, ...]) -> str | None:
    if not args or not args[0].strip() or set(args[0]) & _SYMBOL_FORBIDDEN:
        return None
    return args[0].strip().upper()


def disable_handler(halts: HaltStore) -> Handler:
    async def disable(request: CommandRequest) -> str:
        symbol = _validate_symbol(request.args)
        if symbol is None:
            return "Quel symbole ? Exemple : /disable XAUUSD"
        effect = f"Désactive {symbol} : plus aucun signal ni ordre sur ce marché."
        if len(request.args) < 2 or request.args[1].lower() != CONFIRM:
            return _unconfirmed(f"disable {symbol}", effect)
        await asyncio.to_thread(
            halts.issue,
            HaltCommand(
                market_scope(symbol),
                HaltAction.HALT,
                TELEGRAM,
                f"{symbol} disabled by the operator",
                _actor(request.user_id),
                request.at,
            ),
        )
        return f"{effect} C'est fait. /enable {symbol} pour réactiver."

    return disable


def enable_handler(halts: HaltStore) -> Handler:
    async def enable(request: CommandRequest) -> str:
        symbol = _validate_symbol(request.args)
        if symbol is None:
            return "Quel symbole ? Exemple : /enable XAUUSD"
        effect = f"Réactive {symbol} : les signaux et ordres reprennent sur ce marché."
        if len(request.args) < 2 or request.args[1].lower() != CONFIRM:
            return _unconfirmed(f"enable {symbol}", effect)
        await asyncio.to_thread(
            halts.issue,
            HaltCommand(
                market_scope(symbol),
                HaltAction.RESUME,
                TELEGRAM,
                f"{symbol} re-enabled by the operator",
                _actor(request.user_id),
                request.at,
            ),
        )
        return f"{effect} C'est fait."

    return enable


def mode_handler(events: Engine) -> Handler:
    """Records the requested mode; LIVE is refused here (RM-000)."""

    async def mode(request: CommandRequest) -> str:
        if not request.args:
            return "Quel mode ? " + ", ".join(member.value for member in TradingMode)
        raw = request.args[0].strip().upper()
        try:
            requested = TradingMode(raw)
        except ValueError:
            return f"Mode inconnu : {raw}. Modes possibles : " + ", ".join(
                member.value for member in TradingMode
            )
        if requested is TradingMode.LIVE:
            return (
                "Le mode LIVE ne peut pas être activé depuis Telegram seul (RM-000) : "
                "il exige en plus la variable d'environnement serveur "
                "LIVE_TRADING_ENABLED=true, posée directement sur la machine."
            )
        store = SystemEventStore(events)
        await asyncio.to_thread(
            store.record,
            "mode_command",
            Severity.INFO,
            {"requested": requested.value, "actor": _actor(request.user_id)},
            request.at,
        )
        return (
            f"Mode {requested.value} enregistré ; l'agent l'appliquera à sa prochaine "
            "lecture de l'état."
        )

    return mode
