"""Le registre des ouvertures de jeu scelle : une par jeu, et la trace pour toujours.

**Le defaut que ce module repare.** `research/protocol.SealedSet` comptait ses ouvertures en
memoire. Un nouveau `split_dataset` rendait un jeu scelle neuf, compteur a zero : le meme holdout
pouvait donc etre ouvert indefiniment, et rien ne l'enregistrait. Verifie le 2026-10-10 — apres
avoir ouvert celui du BTCUSD pour valider `vwap_pullback@1.3.0`, `audit_log` ne contenait pas une
seule ligne le mentionnant. Une regle que personne ne peut relire apres coup n'est pas une regle.

**Pourquoi il vit dans `storage` et pas dans `research`.** `backtest` et `research` sont
`OFFLINE_ONLY` dans `tests/test_architecture.py` : aucun module de production ne les importe, et
`storage` en est un. Faire dependre `storage` de `research` retournerait la fleche et casserait
l'invariant. Le sens est donc inverse : `protocol.SealedSet` **recoit** un registre et ne connait
que deux choses de lui, `record(...)`, qui leve si le jeu a deja ete ouvert. Le protocole reste pur
et offline ; la persistance reste dans `storage`.

**La regle appartient a la base.** `identity` est unique, donc la deuxieme ouverture est refusee
par une contrainte et non par la discipline d'un appelant — le meme choix que pour les cles
d'idempotence et les tables d'historique.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tradingagent.storage.models import HoldoutUnlockRow

log = logging.getLogger(__name__)


class HoldoutAlreadyReadError(Exception):
    """The sealed set for this dataset has already been opened once.

    Not a guard to work around: a holdout is a resource that is spent by reading it, and a second
    reading measures a set that is no longer out of sample. A caller that genuinely needs another
    one must seal a different dataset.
    """


@dataclass(frozen=True)
class HoldoutUnlock:
    """One recorded opening, as it comes back out of the table."""

    identity: str
    window_start: datetime
    window_end: datetime
    bars: int
    actor: str
    unlocked_at: datetime


class HoldoutLedger:
    """The only writer of `holdout_unlocks`, and the reader a report needs to prove a point."""

    def __init__(self, engine: Engine, *, actor: str, now: Callable[[], datetime] | None = None):
        self._engine = engine
        self._actor = actor
        self._now = now or (lambda: datetime.now(UTC))

    def record(self, *, identity: str, start: datetime, end: datetime, size: int) -> None:
        """Journal one opening, or refuse because this holdout has already been spent.

        The decision is taken by the unique constraint, not by a prior read: two campaigns
        racing on the same dataset both get a clear answer, and exactly one of them proceeds.
        A pre-check would leave a window where both pass the test and both read.
        """
        if not identity.strip():
            raise ValueError(
                "a holdout unlock needs an identity: without one, nothing tells two datasets "
                "apart and the 'opened once' rule protects nothing"
            )
        stamped = self._now()
        if stamped.utcoffset() != timedelta(0):
            raise ValueError(f"now must be UTC, got {stamped!r}")
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(HoldoutUnlockRow).values(
                        identity=identity,
                        window_start=start,
                        window_end=end,
                        bars=size,
                        actor=self._actor,
                        unlocked_at=stamped,
                    )
                )
        except IntegrityError as error:
            previous = self.previous(identity)
            when = "an earlier run" if previous is None else previous.unlocked_at.isoformat()
            raise HoldoutAlreadyReadError(
                f"{identity}: this holdout was already opened ({when}). A holdout is spent by "
                "being read: a second reading measures a set that is no longer out of sample. "
                "Seal a different dataset if another one is genuinely needed."
            ) from error

    def previous(self, identity: str) -> HoldoutUnlock | None:
        """The recorded opening of this dataset, or None. What a refusal quotes."""
        return self.history(identity)[-1] if self.history(identity) else None

    def history(self, identity: str | None = None) -> list[HoldoutUnlock]:
        """Every opening, oldest first, optionally for one dataset."""
        statement = select(HoldoutUnlockRow).order_by(HoldoutUnlockRow.id)
        if identity is not None:
            statement = statement.where(HoldoutUnlockRow.identity == identity)
        with Session(self._engine) as session:
            return [
                HoldoutUnlock(
                    identity=row.identity,
                    window_start=row.window_start,
                    window_end=row.window_end,
                    bars=row.bars,
                    actor=row.actor,
                    unlocked_at=row.unlocked_at,
                )
                for row in session.scalars(statement)
            ]


def holdout_identity(*, dataset_id: str, market: str, fingerprint: str) -> str:
    """What a holdout is filed under: the dataset it was cut from, not the campaign that read it.

    Built from the frozen dataset rather than from a run, so two campaigns that both decide to
    validate the same market share one entry — which is the whole point: the second one is
    refused because the holdout is already spent, whatever it intends to measure.
    """
    return f"{dataset_id}:{market}:{fingerprint}"
