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
from pathlib import Path

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
#: Where `/restart` leaves its request for the loop to read. The command cannot kill the
#: process that runs it — it would answer into the void — so it persists the demand and the
#: loop closes it at the next cycle.
RESTART_EVENT = "restart_command"

#: The two orders the supervisor understands, written into the file it watches. That file is
#: the only channel that works for the whole stack: the agent cannot stop the MT5 terminal,
#: and it certainly cannot restart the supervisor that launched it.
STOP_EVERYTHING = "arreter"
START_EVERYTHING = "redemarrer"
STACK_ORDERS = {"shutdown": STOP_EVERYTHING, "restart_all": START_EVERYTHING}
#: Where the agent writes an order when the supervisor told it where to write. Empty means
#: nobody supervises this process, and the command then refuses instead of pretending.
CONTROL_FILE_ENV = "TRADINGAGENT_CONTROL_FILE"


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
    if command == "restart":
        return "Confirmer le redémarrage"
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


def local_halt_files(ea_directory: Path | None, symbols: Sequence[str]) -> list[Path]:
    """Les arrêts locaux que les EA ont écrits, un fichier par symbole.

    Le protocole du pont les nomme `<symbole>_halt.txt` sous `control/` : un EA s'arrête tout
    seul quand l'état publié par le backend a plus de trente secondes, et il inscrit ce fichier
    pour survivre à son propre redémarrage.
    """
    if ea_directory is None:
        return []
    return [ea_directory / "control" / f"{symbol}_halt.txt" for symbol in symbols if symbol]


def clear_local_halts(ea_directory: Path | None, symbols: Sequence[str]) -> list[str]:
    """Supprime les arrêts locaux et rend les symboles effectivement relevés.

    Ce n'est pas une commodité, c'est la seule façon de ne pas transformer `/restart_all` en
    piège : redémarrer la pile rend le backend muet plus de trente secondes, ce qui fait
    basculer chaque EA en arrêt local. Au redémarrage suivant du terminal, l'EA relit ce
    fichier et se remet en arrêt — indéfiniment. L'opérateur a demandé le redémarrage, donc
    l'arrêt qu'il provoque est levé dans le même geste, et le compte rendu le dit.
    """
    released: list[str] = []
    for path in local_halt_files(ea_directory, symbols):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            continue
        released.append(path.name.removesuffix("_halt.txt"))
    return released


def stack_handler(
    command: str,
    control_file: Path | None,
    *,
    ea_directory: Path | None = None,
    symbols: Sequence[str] = (),
) -> Handler:
    """Arrête ou redémarre toute la pile, en écrivant l'ordre que le superviseur attend.

    `/restart` redémarre l'agent seul, et la boucle s'en charge. Ces deux commandes-ci vont
    plus loin — terminal MT5, tableau de bord, superviseur — et l'agent n'a aucun moyen de les
    exécuter lui-même : il ne peut pas tuer le terminal, et tuer le superviseur qui l'a lancé
    reviendrait à scier la branche. L'ordre part donc dans un fichier que le superviseur lit à
    chaque battement (`logs/controle.txt`, posé dans `TRADINGAGENT_CONTROL_FILE`).

    Sans superviseur, il n'y a personne pour lire ce fichier : la commande le dit et refuse,
    plutôt que de faire croire à un arrêt qui n'aura pas lieu.

    `/restart_all` lève en plus les arrêts locaux des EA — et seulement lui : `/shutdown`
    laisse tout baisser, arrêts compris, ce qui est exactement ce qu'il promet.
    """
    order = STACK_ORDERS[command]
    if command == "shutdown":
        effect = (
            "Arrête TOUT : l'agent, le tableau de bord, le terminal MT5 et le superviseur.\n"
            "Les positions restent chez le courtier avec leurs stops, mais plus rien ne les\n"
            "surveille, et RIEN ne redémarrera seul. Il faudra relancer à la main."
        )
        done = "🛑 Arrêt de tout demandé · agent, tableau de bord, MT5, superviseur"
    else:
        effect = (
            "Redémarre TOUT : terminal MT5, agent et tableau de bord, dans cet ordre.\n"
            "La coupure dure environ une minute. Les positions et leurs stops ne sont pas\n"
            "touchés, ils vivent chez le courtier. Les arrêts locaux des EA sont levés :\n"
            "sans cela ils resteraient bloqués après le redémarrage."
        )
        done = "🔄 Redémarrage de tout demandé · MT5, agent, tableau de bord"

    async def stack(request: CommandRequest) -> Reply:
        if control_file is None:
            return Reply(
                "Cette commande exige le superviseur, et cet agent n'en a pas :\n"
                "rien ne lirait l'ordre, et rien ne le relancerait.\n"
                "Lance l'agent avec scripts/install_autostart.ps1, puis réessaie."
            )
        if not request.args or request.args[0].lower() != CONFIRM:
            return _asking(effect, command, (), request.at)
        released: list[str] = []
        if order == START_EVERYTHING:
            released = await asyncio.to_thread(clear_local_halts, ea_directory, symbols)
        try:
            await asyncio.to_thread(control_file.write_text, f"{order}\n", encoding="utf-8")
        except OSError as error:
            return Reply(f"Ordre non écrit : {type(error).__name__} · {error}")
        if released:
            return Reply(f"{done} · arrêts locaux levés : {', '.join(released)}")
        return Reply(done)

    return stack


def restart_handler(events: Engine, *, supervised: bool = False) -> Handler:
    """Demande un redémarrage ; la boucle l'honore, le superviseur relance.

    Un gestionnaire de commande ne peut pas tuer le processus qui l'exécute : il répondrait
    dans le vide, et l'opérateur n'aurait aucune confirmation. La demande est donc *persistée*
    (`RESTART_EVENT`) exactement comme `/mode` enregistre le mode voulu ; la boucle la lit au
    cycle suivant, prévient l'opérateur, et s'arrête proprement. Ce qui relance est le
    superviseur (`scripts/supervise_agent.ps1`), qui redémarre tout service arrêté.

    `supervised` ne change pas le comportement, seulement la phrase : un opérateur dont l'agent
    tourne dans une console doit savoir qu'un redémarrage le laissera arrêté. Il est lu dans
    l'environnement, posé par le superviseur lui-même — jamais par `.env`, où l'opérateur
    pourrait le rendre faux.
    """
    if supervised:
        coming_back = (
            "L'agent est supervisé : il revient seul, dans une quinzaine de secondes.\n"
            "Les positions ouvertes et leurs stops ne sont pas touchés."
        )
    else:
        coming_back = (
            "ATTENTION : l'agent n'est pas supervisé. Il s'arrêtera et ne reviendra pas seul.\n"
            "Pour le relancer ensuite : pwsh -File scripts/install_autostart.ps1 -RunNow"
        )
    prompt = (
        "/restart — redémarrer l'agent : arrêt propre, puis relance.\n"
        "Ce qui s'arrête : la boucle, la collecte et les signaux, une vingtaine de secondes.\n"
        "Ce qui continue : le terminal MT5, les positions ouvertes et leurs stops.\n"
        f"{coming_back}"
    )

    async def restart(request: CommandRequest) -> Reply:
        if not request.args or request.args[0].lower() != CONFIRM:
            return _asking(prompt, "restart", (), request.at)
        await asyncio.to_thread(
            SystemEventStore(events).record,
            RESTART_EVENT,
            Severity.INFO,
            {"actor": _actor(request.user_id)},
            request.at,
        )
        return Reply(
            f"Redémarrage demandé : l'agent s'arrête à la fin du cycle en cours.\n{coming_back}"
        )

    return restart


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
