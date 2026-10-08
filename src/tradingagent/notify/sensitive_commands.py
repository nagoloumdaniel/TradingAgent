"""The operator's sensitive commands, each behind an explicit confirmation (TASK-023).

The confirmation convention: a bare command answers with the exact effect it would have,
states what stops and what carries on, and offers one button — or asks for
`/command confirmer`. Nothing is persisted until that confirmation comes back, and every
execution is journalled by CommandService with its author.

A button is not a shortcut around that: it carries the very command line the operator
would have typed, so a click is audited, gated and executed exactly like the typed form.

Semantics (F-019, RM-000, RM-015): a pause and an emergency stop forbid new orders and
keep open positions; only /close_all asks for positions to be closed; the real-money mode
is unreachable from Telegram alone.
"""

import asyncio
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Engine

from tradingagent.core.halt import GLOBAL, market_scope
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import HaltAction, HaltSource, Severity
from tradingagent.notify.commands import CommandRequest, Handler
from tradingagent.notify.replies import Button, Keyboard, Reply, encode_callback, grid
from tradingagent.storage.events import SystemEventStore
from tradingagent.storage.halts import HaltCommand, HaltStore

CONFIRM = "confirmer"
TELEGRAM = HaltSource.TELEGRAM
MODE_EVENT = "mode_command"


def _actor(user_id: int) -> str:
    return f"telegram:{user_id}"


def _confirm(command: str, args: Sequence[str], at: datetime) -> Keyboard:
    """The single button that carries out the action just described."""
    return Keyboard(
        ((Button(_label(command, args), encode_callback(command, (*args, CONFIRM), at)),),)
    )


def _label(command: str, args: Sequence[str]) -> str:
    symbol = args[0] if args else ""
    if command == "close_all":
        return "Confirmer la clôture"
    if command == "emergency_stop":
        return "Confirmer l'arrêt d'urgence"
    if command == "pause":
        return "Confirmer la suspension"
    if command == "resume":
        return "Confirmer la reprise"
    if command == "disable":
        return f"Confirmer la coupure de {symbol}"
    if command == "enable":
        return f"Confirmer la réactivation de {symbol}"
    return f"Confirmer {command}"


def _asking(effect: str, command: str, args: Sequence[str], at: datetime) -> Reply:
    """The effect, spelled out, then how to confirm it — as a button and as text."""
    typed = " ".join((f"/{command}", *args, CONFIRM))
    return Reply(f"{effect}\nPour confirmer : {typed}", _confirm(command, args, at))


def pause_handler(halts: HaltStore) -> Handler:
    prompt = (
        "/pause — suspendre les nouveaux ordres, sans toucher aux positions.\n"
        "Ce qui s'arrête : plus aucun nouvel ordre, sur aucun marché.\n"
        "Ce qui continue : les positions ouvertes sont conservées, stops et\n"
        "  cibles restent actifs.\n"
        "Pour reprendre : /resume."
    )

    async def pause(request: CommandRequest) -> Reply:
        if not request.args or request.args[0].lower() != CONFIRM:
            return _asking(prompt, "pause", (), request.at)
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
        return Reply(
            "C'est fait : les nouveaux ordres sont suspendus sur tous les marchés.\n"
            "Les positions ouvertes sont conservées. Pour reprendre : /resume."
        )

    return pause


def resume_handler(halts: HaltStore) -> Handler:
    prompt = (
        "Reprend l'émission d'ordres après une suspension ou un arrêt d'urgence.\n"
        "Ce qui reprend : les nouveaux ordres, sur tous les marchés.\n"
        "Les positions ouvertes n'ont pas été touchées et restent en place."
    )

    async def resume(request: CommandRequest) -> Reply:
        if not request.args or request.args[0].lower() != CONFIRM:
            return _asking(prompt, "resume", (), request.at)
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
        return Reply(
            "C'est fait : les nouveaux ordres reprennent sur tous les marchés.\n"
            "Les positions ouvertes n'ont pas bougé."
        )

    return resume


def close_all_handler(halts: HaltStore) -> Handler:
    prompt = (
        "FERME toutes les positions ouvertes au marché. Les positions seront\n"
        "réellement clôturées.\n"
        "Ce qui se passe : chaque position ouverte est fermée au prix du marché.\n"
        "Ensuite : les nouveaux ordres sont suspendus. /resume pour reprendre."
    )

    async def close_all(request: CommandRequest) -> Reply:
        if not request.args or request.args[0].lower() != CONFIRM:
            return _asking(prompt, "close_all", (), request.at)
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
        return Reply(
            "Clôture demandée : chaque position ouverte sera fermée au marché.\n"
            "Les nouveaux ordres restent suspendus. /resume pour reprendre."
        )

    return close_all


def emergency_stop_handler(halts: HaltStore) -> Handler:
    prompt = (
        "ARRÊT D'URGENCE : plus aucun nouvel ordre, quelle que soit la source.\n"
        "Les positions ouvertes ne sont pas clôturées automatiquement (RM-015).\n"
        "Pour lever l'arrêt, après contrôle du compte : /resume."
    )

    async def emergency_stop(request: CommandRequest) -> Reply:
        if not request.args or request.args[0].lower() != CONFIRM:
            return _asking(prompt, "emergency_stop", (), request.at)
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
        return Reply(
            "C'est fait : arrêt d'urgence actif, plus aucun nouvel ordre.\n"
            "Les positions ouvertes ne sont pas touchées. /resume pour le lever."
        )

    return emergency_stop


_SYMBOL_FORBIDDEN = set(" \t:,'\"")


def _validate_symbol(args: tuple[str, ...]) -> str | None:
    if not args or not args[0].strip() or set(args[0]) & _SYMBOL_FORBIDDEN:
        return None
    return args[0].strip().upper()


def _known_symbols(markets: Sequence[tuple[str, bool]]) -> list[str]:
    return [symbol for symbol, _enabled in markets]


def _unknown_market(symbol: str, markets: Sequence[tuple[str, bool]]) -> str | None:
    """The refusal for a market the operator does not follow, or None when it is known.

    Re-read at the moment of the click: a button names a market, it does not vouch for it.
    """
    known = _known_symbols(markets)
    if not known or symbol in known:
        return None
    return f"Marché inconnu : {symbol}\nMarchés suivis : {', '.join(known)}."


def _market_choice(
    command: str, markets: Sequence[tuple[str, bool]], at: datetime, opening: str
) -> Reply:
    """One button per configured market: the operator picks, never spells a symbol."""
    rows = grid(
        [Button(symbol, encode_callback(command, (symbol,), at)) for symbol, _enabled in markets]
    )
    return Reply(opening, rows)


def disable_handler(halts: HaltStore, markets: Sequence[tuple[str, bool]] = ()) -> Handler:
    async def disable(request: CommandRequest) -> Reply:
        if not request.args:
            if not markets:
                return Reply("Quel symbole ? Exemple : /disable XAUUSD")
            return _market_choice(
                "disable",
                markets,
                request.at,
                "Quel marché désactiver ?\n"
                "Désactiver un marché : plus aucun signal ni ordre dessus.\n"
                "Les positions déjà ouvertes ne sont pas clôturées.",
            )
        symbol = _validate_symbol(request.args)
        if symbol is None:
            return Reply("Quel symbole ? Exemple : /disable XAUUSD")
        unknown = _unknown_market(symbol, markets)
        if unknown is not None:
            return Reply(unknown)
        effect = (
            f"Désactive {symbol} : plus aucun signal ni ordre sur ce marché.\n"
            "Les positions déjà ouvertes ne sont pas clôturées."
        )
        if len(request.args) < 2 or request.args[1].lower() != CONFIRM:
            return _asking(effect, "disable", (symbol,), request.at)
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
        return Reply(
            f"C'est fait : {symbol} est désactivé, plus aucun signal ni ordre.\n"
            f"/enable {symbol} pour réactiver."
        )

    return disable


def enable_handler(halts: HaltStore, markets: Sequence[tuple[str, bool]] = ()) -> Handler:
    async def enable(request: CommandRequest) -> Reply:
        if not request.args:
            if not markets:
                return Reply("Quel symbole ? Exemple : /enable XAUUSD")
            return _market_choice(
                "enable",
                markets,
                request.at,
                "Quel marché réactiver ?\n"
                "Réactiver un marché : les signaux et les ordres reprennent dessus.",
            )
        symbol = _validate_symbol(request.args)
        if symbol is None:
            return Reply("Quel symbole ? Exemple : /enable XAUUSD")
        unknown = _unknown_market(symbol, markets)
        if unknown is not None:
            return Reply(unknown)
        effect = f"Réactive {symbol} : les signaux et ordres reprennent sur ce marché."
        if len(request.args) < 2 or request.args[1].lower() != CONFIRM:
            return _asking(effect, "enable", (symbol,), request.at)
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
        return Reply(
            f"C'est fait : {symbol} est réactivé, les signaux et ordres reprennent.\n"
            "Les autres marchés n'ont pas bougé."
        )

    return enable


# What each mode means for the operator's money, in the order of exposure.
MODE_CHOICES: tuple[tuple[TradingMode, str, str], ...] = (
    (TradingMode.OBSERVATION, "OBSERVATION", "il analyse et n'envoie rien, aucun ordre préparé"),
    (TradingMode.SIGNAL, "SIGNAL", "il envoie les signaux sur Telegram, aucun ordre passé"),
    (TradingMode.PAPER, "PAPER", "remplissages simulés, aucun ordre chez le courtier"),
    (TradingMode.DEMO, "DÉMO", "ordres réels sur le compte de démonstration, argent fictif"),
    (TradingMode.LIVE, "RÉEL", "argent réel, exige les 9 portes et l'accord du serveur"),
)

MODE_CONSEQUENCE = {mode: consequence for mode, _label_, consequence in MODE_CHOICES}
MODE_SHORT = {mode: label for mode, label, _consequence in MODE_CHOICES}
MODE_LABEL_WIDTH = max(len(label) for _mode, label, _consequence in MODE_CHOICES)

LIVE_REFUSAL = (
    "Le mode LIVE ne peut pas être activé depuis Telegram seul (RM-000) :\n"
    "il exige la variable serveur LIVE_TRADING_ENABLED=true, posée sur la machine,\n"
    "et les 9 portes de promotion franchies. Rien n'a été changé."
)


def _mode_buttons(at: datetime) -> Keyboard:
    """One button per mode, so the choice is a tap and never a word to spell right."""
    choices = [
        Button(label, encode_callback("mode", (mode.value,), at))
        for mode, label, _consequence in MODE_CHOICES
    ]
    return grid(choices)


def _mode_menu(at: datetime, current: str | None) -> Reply:
    """Every mode, its consequence, and where the agent stands today — then the buttons."""
    if current is None:
        standing = "Aucun changement de mode enregistré depuis le démarrage."
    else:
        standing = (
            f"Dernier mode demandé : {current}.\n"
            "  L'agent l'applique à sa prochaine lecture de l'état."
        )
    lines = [
        "/mode — ce que l'agent a le droit de faire.",
        "",
        standing,
        "",
        *[
            f"{label:<{MODE_LABEL_WIDTH}} : {consequence}."
            for _mode, label, consequence in MODE_CHOICES
        ],
        "",
        "Appuie sur un bouton : le changement est journalisé aussitôt.",
    ]
    return Reply("\n".join(lines), _mode_buttons(at))


def _recorded_mode(events: Engine) -> str | None:
    event = SystemEventStore(events).latest(MODE_EVENT)
    if event is None:
        return None
    requested = event.detail.get("requested")
    return None if requested is None else str(requested)


def mode_handler(events: Engine) -> Handler:
    """Records the requested mode; LIVE is refused here (RM-000)."""

    async def mode(request: CommandRequest) -> Reply:
        if not request.args:
            current = await asyncio.to_thread(_recorded_mode, events)
            return _mode_menu(request.at, current)
        raw = request.args[0].strip().upper()
        try:
            requested = TradingMode(raw)
        except ValueError:
            known = ", ".join(member.value for member in TradingMode)
            return Reply(
                f"Mode inconnu : {raw}.\nModes possibles : {known}.",
                _mode_buttons(request.at),
            )
        if requested is TradingMode.LIVE:
            return Reply(LIVE_REFUSAL, _mode_buttons(request.at))
        store = SystemEventStore(events)
        await asyncio.to_thread(
            store.record,
            MODE_EVENT,
            Severity.INFO,
            {"requested": requested.value, "actor": _actor(request.user_id)},
            request.at,
        )
        return Reply(
            f"C'est fait : mode {requested.value} enregistré.\n"
            f"Conséquence : {MODE_CONSEQUENCE[requested]}.\n"
            "L'agent l'applique à sa prochaine lecture de l'état."
        )

    return mode
