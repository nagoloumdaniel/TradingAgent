"""Le VWAP de session : une moyenne pondérée par le volume, remise à zéro à une heure choisie.

Le VWAP n'est pas une moyenne mobile. Sa valeur dépend entièrement du point de départ, et deux
ancres différentes donnent deux indicateurs différents sur la même série : l'ancre est donc un
paramètre explicite, jamais un choix implicite caché dans une boucle.

Deux règles de lecture, et elles viennent toutes les deux d'un défaut réel :

* **Une série sans volume n'a pas de VWAP.** `volume is None` veut dire « non enregistré », et
  pondérer une moyenne par des poids inconnus ne produit pas une approximation : ça produit un
  nombre qui n'a pas de sens. Ces barres rendent `None`.
* **Un volume nul en revanche est une mesure.** Une minute sans échange compte pour zéro et ne
  remet pas le cumul à zéro : la dernière valeur valide reste vraie, puisqu'aucun prix n'a été
  traité entre-temps.
"""

from collections.abc import Sequence
from datetime import datetime

from tradingagent.indicators._checks import require_finite

#: La journée UTC est l'ancre par défaut : le BTC cote 24/7, et 00:00 UTC ne bouge pas avec
#: l'heure d'été. Une autre ancre s'exprime en minutes depuis minuit.
DEFAULT_SESSION_ANCHOR_MINUTES = 0

_DAY_MINUTES = 24 * 60


def typical_price(high: float, low: float, close: float) -> float:
    """Le prix de référence d'une barre : `(haut + bas + clôture) / 3`."""
    require_finite((high, low, close))
    return (high + low + close) / 3.0


def vwap(
    open_times: Sequence[datetime],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float | None],
    *,
    session_anchor_minutes: int = DEFAULT_SESSION_ANCHOR_MINUTES,
) -> list[float | None]:
    """VWAP aligné sur les entrées, remis à zéro à chaque nouvelle session.

    La session change quand l'horodatage franchit un multiple de 24 h décalé de
    `session_anchor_minutes`. La remise à zéro est portée par l'horodatage des barres, jamais
    par leur position : c'est ce qui rend le résultat indépendant du moment où un
    téléchargement a commencé.

    Les `open_times` sont exigés en UTC.
    """
    _require_aligned(open_times, highs, lows, closes, volumes)
    for series in (highs, lows, closes):
        require_finite(series)
    for moment in open_times:
        if moment.utcoffset() is None:
            # Un datetime naïf est une erreur d'appel, pas un instant : le deviner ferait
            # glisser toutes les frontières de session en silence.
            raise ValueError(f"open_times must be timezone-aware, got {moment!r}")
    for volume in volumes:
        if volume is None:
            continue
        if volume < 0:
            raise ValueError(f"a volume must not be negative, got {volume!r}")

    result: list[float | None] = []
    session: int | None = None
    volume_sum = 0.0
    weighted_sum = 0.0
    for moment, high, low, close, volume in zip(
        open_times, highs, lows, closes, volumes, strict=True
    ):
        # Un instant reste le même instant : `timestamp()` porte déjà l'instant absolu, donc
        # 02:00+02:00 et 00:00Z donnent le même nombre sans reconversion. Le `astimezone(UTC)`
        # par barre ne changeait pas un chiffre — les tests le prouvent sur quatre écritures du
        # même instant — et coûtait une conversion à chaque bougie de chaque fenêtre.
        current = _session_index(moment, session_anchor_minutes)
        if current != session:
            session = current
            volume_sum = 0.0
            weighted_sum = 0.0
        if volume is None:
            # Le poids est inconnu : la barre rend la session indéfinie plutôt que d'avancer
            # un cumul qui ne veut plus rien dire. La session suivante repart proprement.
            result.append(None)
            continue
        # Le prix typique est écrit ici au lieu d'appeler `typical_price` : la finitude des
        # trois séries vient d'être vérifiée en entier, donc revalider chaque triplet à chaque
        # barre ne protège rien de plus — c'était 7,84 millions d'appels sur 8,00 millions.
        # `typical_price` garde son propre contrat, exposé, pour ses appelants directs.
        weighted_sum += ((high + low + close) / 3.0) * volume
        volume_sum += volume
        result.append(None if volume_sum <= 0 else weighted_sum / volume_sum)
    return result


def _session_index(moment: datetime, anchor_minutes: int) -> int:
    """Le numéro de session d'un instant : change à chaque frontière de 24 h décalée.

    `moment` doit être conscient du fuseau — `vwap` le vérifie avant d'arriver ici — parce que
    `timestamp()` lit l'instant absolu, et qu'un datetime naïf serait interprété en heure locale.
    """
    seconds = int(moment.timestamp()) - anchor_minutes * 60
    return seconds // (_DAY_MINUTES * 60)


def _require_aligned(*series: Sequence[object]) -> None:
    lengths = {len(item) for item in series}
    if len(lengths) > 1:
        raise ValueError(f"every series must have the same length, got {sorted(lengths)}")
