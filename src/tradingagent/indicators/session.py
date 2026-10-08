"""À quelle séance de marché appartient un instant, en UTC.

Un opérateur ne lit pas la même chose à 03:00 et à 14:00 : la séance dit quand le marché est
liquide, et « quand » est une information que la stratégie n'a pas aujourd'hui. Le dépôt
connaît les **horaires d'ouverture** (`data/market_calendar.py`) mais pas la **nature** de la
séance en cours.

Les fenêtres sont exprimées en UTC, donc en heure d'hiver pour Londres et New York. Un marché
qui vit à l'heure locale dérive d'une heure une partie de l'année, et c'est assumé : la
source des barres est horodatée en UTC, et une règle de recherche doit être reproductible
avant d'être fine. Prétendre corriger un décalage qu'on n'a pas mesuré serait pire que de le
nommer.

Les fenêtres sont **demi-ouvertes** : une séance qui commence à 07:00 ne contient pas 07:00
deux fois quand deux d'entre elles se touchent, et une qui finit à 16:00 n'inclut pas 16:00.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from enum import StrEnum


class Session(StrEnum):
    """La séance en cours, ou l'absence de séance."""

    TOKYO = "tokyo"
    LONDON = "london"
    OVERLAP = "overlap"  # Londres et New York ouvertes en même temps : la fenêtre la plus liquide
    NEW_YORK = "new_york"
    OFF = "off"


@dataclass(frozen=True, slots=True)
class Window:
    """Une fenêtre intra-journalière, en UTC, bornes demi-ouvertes `[start, end)`.

    `end` peut être plus petit que `start` : la fenêtre franchit alors minuit, ce qui est le
    cas normal pour Tokyo vue de l'Europe.
    """

    session: Session
    start: time
    end: time

    def contains(self, moment: datetime) -> bool:
        """Vrai si `moment` tombe dans la fenêtre, bornes demi-ouvertes.

        La comparaison se fait sur l'heure UTC, jamais sur l'écriture locale : deux écritures
        du même instant doivent donner la même réponse.
        """
        # `.time()` et non `.timetz()` : après normalisation en UTC, l'offset est nul et une
        # heure consciente du fuseau ne se compare pas à une heure naïve.
        current = moment.astimezone(UTC).time()
        if self.start <= self.end:
            return self.start <= current < self.end
        # La fenêtre franchit minuit : elle est l'union de deux intervalles.
        return current >= self.start or current < self.end


#: Ordonnées de la plus étroite à la plus large : la première qui contient l'instant gagne.
#:
#: L'ordre porte une décision, pas un détail d'écriture. Trois fenêtres se chevauchent :
#: Londres et New York contiennent l'overlap, et Tokyo contient le début de Londres. Classer
#: par la plus étroite donne à chaque instant l'étiquette la plus spécifique vraie :
#: 13:00-16:00 est un *overlap* (la fenêtre la plus liquide), 07:00-09:00 est *Londres* — et
#: non Tokyo, sinon la séance européenne serait invisible dans les données. Tokyo ne capte
#: donc que 00:00-07:00.
DEFAULT_WINDOWS: tuple[Window, ...] = (
    Window(Session.OVERLAP, time(13, 0), time(16, 0)),
    Window(Session.LONDON, time(7, 0), time(16, 0)),
    Window(Session.NEW_YORK, time(13, 0), time(22, 0)),
    Window(Session.TOKYO, time(0, 0), time(9, 0)),
)


def session_at(moment: datetime, windows: Sequence[Window] = DEFAULT_WINDOWS) -> Session:
    """La séance en cours à `moment`, ou `Session.OFF` si aucune fenêtre ne le contient.

    L'instant est ramené en UTC avant comparaison : un horodatage écrit avec un décalage
    désigne le même moment, et le classer selon son écriture serait une erreur silencieuse.

    La **première** fenêtre qui contient l'instant gagne. L'ordre de `windows` est donc
    significatif, et `DEFAULT_WINDOWS` place volontairement l'overlap avant Londres et New
    York, qui le contiennent tous les deux : sans cet ordre, l'étiquette de 13:00 dépendrait
    de la façon dont la liste a été écrite.
    """
    if moment.utcoffset() is None:
        raise ValueError(f"moment must be timezone-aware, got {moment!r}")
    for window in windows:
        if window.contains(moment):
            return window.session
    return Session.OFF


def sessions_for(
    moments: Sequence[datetime], windows: Sequence[Window] = DEFAULT_WINDOWS
) -> list[Session]:
    """La séance de chaque instant, dans l'ordre, sans lecture d'horloge."""
    return [session_at(moment, windows) for moment in moments]
