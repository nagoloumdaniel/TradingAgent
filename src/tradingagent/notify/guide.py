"""The guide: a menu of buttons, and one page per command behind each of them.

The operator asked for a palette they can *tap* rather than spell, and for each button to
answer with its own page: what it does, one example to copy, and the button that performs
it. This module is that text.

Two rules hold it together.

*The pages are written here, in Telegram's own vocabulary, and never interpolated from
market data.* Bold for the action, `<code>` for what to type, `<blockquote>` for what
happens to the positions. That is what makes markup safe here at all: the invariant the
project defends is not "no tags", it is "nothing coming from data can break a message" —
and a page made of constants cannot be broken by a price or a symbol.

*Every page ends on the button that does the thing.* The operator reads the consequence
and the example in the same message that offers the action, which is exactly the
confirmation convention of the sensitive commands: the effect is stated before anything
executes, and a tap replays the audited path of the typed command.

This module knows nothing about routing: the caller hands it the palette it has built
(`Menu`), so `commands.py` and `guide.py` never import each other.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from tradingagent.notify.replies import (
    HTML,
    Button,
    Keyboard,
    Reply,
    encode_callback,
    grid,
)

BACK_LABEL = "◀ Menu"
MENU_HEADER = "🧭 <b>Commandes</b>"
OTHER_GROUP = "AUTRES"
OTHER_TAGLINE = "sans fiche détaillée"
#: Two per row: a phone shows two command names comfortably, and the palette is long.
MENU_COLUMNS = 2
LINE_WIDTH = 80


@dataclass(frozen=True)
class Group:
    """One section of the menu: its heading, its tagline, and the commands it holds."""

    label: str
    tagline: str
    names: tuple[str, ...]


@dataclass(frozen=True)
class Page:
    """One command's page: what it does, how to type it, and the button that runs it.

    `action` is what the page's main button sends: the command, its arguments and the
    label. `None` means the command opens a choice of its own (`/disable`, `/mode`,
    `/report`), and the page then offers exactly that first step.
    """

    detail: str
    example: tuple[str, ...]
    keeps: str
    action: tuple[str, tuple[str, ...], str] | None


#: How a command that opens a choice labels the button that opens it.
OPENS_WITH: dict[str, str] = {
    "disable": "Choisir un marché",
    "enable": "Choisir un marché",
    "mode": "Choisir un mode",
    "report": "Choisir une période",
}

#: Pages for the commands that change something. The text under each title is the
#: confirmation the operator reads before the button below it acts.
ACTION_PAGES: dict[str, Page] = {
    "pause": Page(
        detail="Suspend les nouveaux ordres, sur tous les marchés.",
        example=("/pause", "/pause confirmer"),
        keeps="Les positions ouvertes sont conservées ; stops et cibles restent actifs.",
        action=("pause", ("confirmer",), "Confirmer la suspension"),
    ),
    "resume": Page(
        detail="Reprend l'émission d'ordres après une suspension ou un arrêt d'urgence.",
        example=("/resume", "/resume confirmer"),
        keeps="Les positions ouvertes n'ont pas bougé et restent en place.",
        action=("resume", ("confirmer",), "Confirmer la reprise"),
    ),
    "close_all": Page(
        detail="FERME toutes les positions ouvertes au prix du marché, puis suspend les ordres.",
        example=("/close_all", "/close_all confirmer"),
        keeps="⚠️ Les positions sont réellement clôturées, avec l'argent engagé.",
        action=("close_all", ("confirmer",), "Confirmer la clôture"),
    ),
    "emergency_stop": Page(
        detail="Arrêt d'urgence : plus aucun nouvel ordre, quelle que soit la source.",
        example=("/emergency_stop", "/emergency_stop confirmer"),
        keeps="Les positions ouvertes ne sont PAS fermées (RM-015). /resume pour lever.",
        action=("emergency_stop", ("confirmer",), "Confirmer l'arrêt d'urgence"),
    ),
    "disable": Page(
        detail="Coupe un marché : plus aucun signal ni ordre dessus, les autres continuent.",
        example=("/disable XAUUSD", "/disable XAUUSD confirmer"),
        keeps="Les positions déjà ouvertes ne sont pas clôturées.",
        action=None,
    ),
    "enable": Page(
        detail="Rouvre un marché précédemment coupé.",
        example=("/enable XAUUSD", "/enable XAUUSD confirmer"),
        keeps="Les autres marchés ne sont pas touchés.",
        action=None,
    ),
    "mode": Page(
        detail="Montre les modes et ce que chacun autorise, puis permet d'en choisir un.",
        example=("/mode", "/mode DEMO"),
        keeps="DEMO engage de l'argent fictif ; RÉEL est refusé depuis Telegram seul (RM-000).",
        action=None,
    ),
    "restart": Page(
        detail="Redémarre l'agent seul : arrêt propre, puis relance par le superviseur.",
        example=("/restart", "/restart confirmer"),
        keeps="Le terminal MT5, les positions ouvertes et leurs stops ne sont pas touchés.",
        action=("restart", ("confirmer",), "Confirmer le redémarrage"),
    ),
    "shutdown": Page(
        detail="Arrête TOUT : agent, tableau de bord, terminal MT5 et superviseur.",
        example=("/shutdown", "/shutdown confirmer"),
        keeps="Les positions restent chez le courtier avec leurs stops, mais plus rien ne les "
        "surveille, et rien ne redémarrera seul.",
        action=("shutdown", ("confirmer",), "Confirmer l'arrêt de tout"),
    ),
    "restart_all": Page(
        detail="Redémarre TOUT : terminal MT5, agent et tableau de bord, dans cet ordre.",
        example=("/restart_all", "/restart_all confirmer"),
        keeps="Positions et stops ne sont pas touchés ; la coupure dure environ une minute.",
        action=("restart_all", ("confirmer",), "Confirmer le redémarrage de tout"),
    ),
    "report": Page(
        detail="Compose le rapport de la période demandée depuis la base.",
        example=("/report", "/report weekly"),
        keeps="Lecture seule : rien n'est modifié.",
        action=None,
    ),
}

#: What the read-only commands answer, in one line each; the example is the signature.
READ_DETAILS: dict[str, str] = {
    "status": "Mode courant, arrêt d'urgence et quarantaines.",
    "markets": "Marchés suivis, actifs ou non, et fraîcheur de la dernière bougie.",
    "marche": "Un marché : suivi, position, haltes et calendrier appris.",
    "signals": "Les derniers signaux produits, avec leur état.",
    "positions": "Les positions ouvertes et leur prix d'entrée.",
    "performance": "Les trades clôturés, le taux de réussite et le PnL.",
    "propositions": "Ce que l'IA propose, et la décision prise sur chaque proposition.",
    "portes": "Les portes de promotion encore manquantes pour une stratégie.",
}


def _wrap(text: str) -> tuple[str, ...]:
    """Wrap on spaces: no line runs off a phone screen once the tags are stripped."""
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > LINE_WIDTH and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return tuple(lines)


def menu(groups: Sequence[Group], at: datetime) -> Reply:
    """The palette as buttons: one per command, in the reading order of the usages."""
    entries = [name for group in groups for name in group.names]
    text = [MENU_HEADER, ""]
    for group in groups:
        if group.names:
            text.append(f"▸ <b>{group.label}</b> — {group.tagline}")
    text += [
        "",
        "Choisis une commande : elle s'ouvre avec son exemple et son bouton.",
        "Le menu s'ouvre aussi par le bouton en bas à gauche ; « / » propose la liste.",
    ]
    # Each button opens that command's *page*, not the command: the operator reads what it
    # does, sees the example, and only then taps the confirmation underneath.
    buttons = [Button(f"/{name}", encode_callback("aide", (name,), at)) for name in entries]
    if not buttons:
        return Reply("\n".join(text), parse_mode=HTML)
    return Reply("\n".join(text), grid(buttons, MENU_COLUMNS), parse_mode=HTML)


def page(name: str, at: datetime, *, description: str = "") -> Reply:
    """One command's page, or the little page an undocumented command deserves.

    A read-only command gets its button too: pressing it runs the command, which is the
    point of a tap-only palette. An actionable one gets its confirmation button — the page
    *is* the warning the confirmation convention requires before a sensitive command runs.
    """
    specific = ACTION_PAGES.get(name)
    if specific is None and name in READ_DETAILS:
        specific = Page(
            detail=READ_DETAILS[name],
            example=(f"/{name}",),
            keeps="Lecture seule : rien n'est modifié.",
            action=(name, (), "Afficher"),
        )
    if specific is None:
        body = [f"<b>/{name}</b>", "", *_wrap(description or "Commande enregistrée.")]
        return Reply("\n".join(body), _back(at), parse_mode=HTML)

    blocks = [f"<b>/{name}</b>", "", *_wrap(specific.detail), ""]
    blocks.append("<blockquote>" + "\n".join(_wrap(specific.keeps)) + "</blockquote>")
    blocks += ["", "<b>Exemple</b>", "<code>" + "\n".join(specific.example) + "</code>"]
    return Reply("\n".join(blocks), _actions(name, specific, at), parse_mode=HTML)


def _actions(name: str, specific: Page, at: datetime) -> Keyboard:
    """The page's buttons: the action first, the way back under it."""
    if specific.action is not None:
        command, args, label = specific.action
        return Keyboard(((Button(label, encode_callback(command, args, at)),), *_back(at).rows))
    opens = OPENS_WITH.get(name, "Ouvrir")
    return Keyboard(((Button(opens, encode_callback(name, (), at)),), *_back(at).rows))


def _back(at: datetime) -> Keyboard:
    return Keyboard(((Button(BACK_LABEL, encode_callback("help", (), at)),),))
