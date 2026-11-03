"""Un motif de refus du moteur de risque, dit en français clair.

Le moteur stocke ses motifs sous la forme `contrôle: détail; contrôle: détail` (voir
`risk.engine`), en anglais et avec des chiffres bruts. Cette fonction pure les relit pour
l'opérateur (tableau de bord) et pour l'IA d'analyse : le même texte explicite sert aux deux.
Un motif inconnu n'est jamais inventé, il est rendu tel quel.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

#: Titre court (pour regrouper) et phrase d'explication de chaque contrôle.
TITLES: dict[str, str] = {
    "not_halted": "Arrêt de sécurité actif",
    "stop_loss": "Stop-loss invalide",
    "entry_zone": "Prix hors de la zone d'entrée",
    "slippage": "Glissement attendu trop grand",
    "daily_loss": "Perte du jour au plafond",
    "weekly_loss": "Perte de la semaine au plafond",
    "drawdown": "Drawdown au plafond",
    "open_positions": "Trop de positions ouvertes",
    "market_positions": "Déjà une position ouverte sur ce marché",
    "total_exposure": "Exposition totale trop élevée",
    "trades_today": "Nombre de trades du jour atteint",
    "spread": "Spread trop large",
    "total_cost": "Coûts de transaction trop élevés",
    "margin": "Marge insuffisante",
    "account_currency": "Devise du compte inattendue",
    "trading_hours": "Marché fermé",
    "cooldown": "Pause après pertes consécutives",
    "daily_loss_streak": "Série de pertes du jour atteinte",
    "live_eligibility": "Compte réel non éligible",
}

_NUMBER = r"-?\d+(?:\.\d+)?"


@dataclass(frozen=True)
class _Rule:
    pattern: re.Pattern[str]
    say: Callable[[re.Match[str]], str]


def _rule(pattern: str, say: Callable[[re.Match[str]], str]) -> _Rule:
    return _Rule(re.compile(pattern), say)


_DETAILS: dict[str, list[_Rule]] = {
    "stop_loss": [
        _rule(r"^no stop-loss$", lambda m: "le signal n'a aucun stop-loss"),
        _rule(
            rf"^stop ({_NUMBER}) is not protective for a (\w+) quoted at ({_NUMBER})$",
            lambda m: (
                f"le stop {m[1]} ne protège pas une position {m[2]} cotée à {m[3]} "
                "(il est du mauvais côté du prix)"
            ),
        ),
        _rule(
            rf"^stop distance ({_NUMBER}) below the broker minimum ({_NUMBER})$",
            lambda m: f"le stop est à {m[1]} du prix, sous le minimum du courtier ({m[2]})",
        ),
    ],
    "entry_zone": [
        _rule(
            rf"^price ({_NUMBER}) outside \[({_NUMBER}), ({_NUMBER})\]$",
            lambda m: (
                f"le prix a bougé à {m[1]}, hors de la zone d'entrée {m[2]} à {m[3]} : "
                "l'occasion est passée"
            ),
        ),
    ],
    "slippage": [
        _rule(
            rf"^expected slippage ({_NUMBER}), limit ({_NUMBER}) \((\S+) x spread ({_NUMBER})\)$",
            lambda m: f"glissement attendu {m[1]} pour une limite de {m[2]} (spread {m[4]})",
        ),
    ],
    "spread": [
        _rule(
            rf"^spread ({_NUMBER}), limit ({_NUMBER})$",
            lambda m: f"spread de {m[1]} alors que la limite est {m[2]} : coût d'entrée trop élevé",
        ),
    ],
    "open_positions": [
        _rule(
            r"^(\d+) open, limit (\d+)$",
            lambda m: f"{m[1]} position(s) ouverte(s), limite {m[2]}",
        ),
    ],
    "market_positions": [
        _rule(
            r"^(\d+) open on (\S+), limit (\d+)$",
            lambda m: f"{m[1]} position(s) déjà ouverte(s) sur {m[2]}, limite {m[3]}",
        ),
    ],
    "trades_today": [
        _rule(
            r"^(\d+) today, limit (\d+)$",
            lambda m: f"{m[1]} trade(s) aujourd'hui, limite {m[2]} : plus d'entrée avant demain",
        ),
    ],
    "margin": [
        _rule(r"^margin for one lot unavailable$", lambda m: "la marge d'un lot est inconnue"),
        _rule(
            rf"^minimum lot needs ({_NUMBER}) EUR, ({_NUMBER}) usable$",
            lambda m: f"le lot minimum exige {m[1]} € de marge, {m[2]} € sont disponibles",
        ),
    ],
    "trading_hours": [
        _rule(r"^market (.+)$", lambda m: f"le marché n'est pas ouvert ({m[1]})"),
    ],
    "account_currency": [
        _rule(
            r"^account in (\S+), expected (\S+)$",
            lambda m: f"le compte est en {m[1]} au lieu de {m[2]}",
        ),
    ],
    "total_exposure": [
        _rule(
            rf"^exposure ({_NUMBER}) EUR \(open ({_NUMBER}) \+ order ({_NUMBER})\), "
            rf"limit ({_NUMBER}) EUR$",
            lambda m: f"exposition {m[1]} € (ouvert {m[2]} € + ordre {m[3]} €), limite {m[4]} €",
        ),
    ],
    "total_cost": [
        _rule(
            r"^cost (\S+).*, limit (\S+)$",
            lambda m: f"coût estimé {m[1]} pour un maximum de {m[2]}",
        ),
    ],
    "cooldown": [
        _rule(
            r"^(\d+) consecutive losses, paused until (\S+)$",
            lambda m: f"{m[1]} pertes consécutives : pause jusqu'à {m[2]}",
        ),
    ],
    "daily_loss_streak": [
        _rule(
            r"^(\d+) consecutive loss\(es\) today( on \S+)?, limit (\d+)$",
            lambda m: (
                f"{m[1]} pertes consécutives aujourd'hui{m[2] or ''}, limite {m[3]} : "
                "trades arrêtés jusqu'à demain 00:00 UTC"
            ),
        ),
    ],
}

for _name in ("daily_loss", "weekly_loss", "drawdown"):
    _DETAILS[_name] = [
        _rule(
            rf"^loss ({_NUMBER}) EUR(?: \+ ({_NUMBER}) EUR at stake)?, limit ({_NUMBER}) EUR$",
            lambda m: (
                f"perte {m[1]} €"
                + (f" + {m[2]} € risqués par ce trade" if m[2] else "")
                + f", limite {m[3]} €"
            ),
        ),
    ]


def split_reason(reason: str) -> list[tuple[str | None, str]]:
    """`a: x; b: y` -> [(a, x), (b, y)]. A segment with no known check name keeps `None`."""
    parts: list[tuple[str | None, str]] = []
    for segment in reason.split("; "):
        name, separator, detail = segment.partition(": ")
        if separator and name in TITLES:
            parts.append((name, detail))
        elif parts:
            previous_name, previous = parts[-1]
            parts[-1] = (previous_name, f"{previous}; {segment}")
        else:
            parts.append((None, segment))
    return parts


def explain_detail(name: str, detail: str) -> str:
    for rule in _DETAILS.get(name, []):
        match = rule.pattern.match(detail)
        if match:
            return rule.say(match)
    return detail


def refusal_causes(reason: str | None) -> list[str]:
    """The distinct French titles behind a stored refusal reason, in order."""
    if not reason or not reason.strip():
        return ["Motif non enregistré"]
    titles: list[str] = []
    for name, detail in split_reason(reason.strip()):
        title = TITLES[name] if name else detail
        if title not in titles:
            titles.append(title)
    return titles


def explain_refusal(reason: str | None) -> str:
    """The whole refusal as plain French: « Spread trop large — spread de 0.5 alors que… »."""
    if not reason or not reason.strip():
        return "Motif non enregistré"
    sentences = []
    for name, detail in split_reason(reason.strip()):
        if name is None:
            sentences.append(detail)
        else:
            sentences.append(f"{TITLES[name]} — {explain_detail(name, detail)}")
    return " ; ".join(sentences)
