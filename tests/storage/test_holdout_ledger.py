"""Un jeu scelle s'ouvre une fois, et la trace reste — le defaut du 2026-10-10.

**Ce que ces tests verrouillent.** `protocol.SealedSet` comptait ses ouvertures en memoire, donc
un nouveau `split_dataset` rendait un jeu scelle neuf et le meme holdout pouvait etre lu
indefiniment. Apres avoir ouvert celui du BTCUSD pour valider `vwap_pullback@1.3.0`, `audit_log`
ne contenait aucune ligne le mentionnant : la regle « un holdout se depense » n'etait tenue par
rien de persistant.

Trois proprietes, et chacune compte :

1. **la trace survit au processus** — une deuxieme tentative, meme apres reconstruction complete
   de l'objet, est refusee ;
2. **le refus ne comptabilise rien** — un holdout deja depense n'a pas ete rouvert une fois de
   plus, et l'ecrire ferait mentir la trace dans l'autre sens ;
3. **la regle appartient a la base** — c'est la contrainte d'unicite qui tranche, pas une lecture
   prealable qui laisserait une fenetre ou deux campagnes passent toutes les deux.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, insert, text

from tradingagent.storage.holdout import (
    HoldoutAlreadyReadError,
    HoldoutLedger,
    holdout_identity,
)

NOW = datetime(2026, 10, 11, 1, 30, tzinfo=UTC)
IDENTITY = holdout_identity(
    dataset_id="mt5-BTCUSD-M15-2026-10-09", market="BTCUSD", fingerprint="a" * 64
)


def _ledger(engine: Engine, at: datetime = NOW) -> HoldoutLedger:
    return HoldoutLedger(engine, actor="test/lead", now=lambda: at)


def test_a_first_opening_is_recorded(engine: Engine) -> None:
    ledger = _ledger(engine)

    ledger.record(
        identity=IDENTITY,
        start=datetime(2026, 6, 5, 23, 15, tzinfo=UTC),
        end=datetime(2026, 10, 9, 1, 45, tzinfo=UTC),
        size=12001,
    )

    history = ledger.history(IDENTITY)
    assert len(history) == 1
    assert history[0].bars == 12001
    assert history[0].actor == "test/lead"
    assert history[0].unlocked_at == NOW


def test_a_second_opening_is_refused(engine: Engine) -> None:
    """The one that matters: any later campaign reading the same holdout is stopped."""
    _ledger(engine).record(
        identity=IDENTITY,
        start=datetime(2026, 6, 5, 23, 15, tzinfo=UTC),
        end=datetime(2026, 10, 9, 1, 45, tzinfo=UTC),
        size=12001,
    )

    with pytest.raises(HoldoutAlreadyReadError) as raised:
        _ledger(engine, at=datetime(2026, 10, 12, tzinfo=UTC)).record(
            identity=IDENTITY,
            start=datetime(2026, 6, 5, 23, 15, tzinfo=UTC),
            end=datetime(2026, 10, 9, 1, 45, tzinfo=UTC),
            size=12001,
        )

    assert "already opened" in str(raised.value)
    assert "2026-10-11" in str(raised.value), "the refusal must name when it was spent"
    assert len(_ledger(engine).history(IDENTITY)) == 1, "a refusal records nothing"


def test_the_database_refuses_it_not_a_preliminary_read(engine: Engine) -> None:
    """Two writers, one winner: the unique constraint decides, so there is no race window."""
    from sqlalchemy.exc import IntegrityError

    from tradingagent.storage.models import HoldoutUnlockRow

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO holdout_unlocks "
                "(identity, window_start, window_end, bars, actor, unlocked_at) "
                "VALUES (:i, :s, :e, :b, :a, :u)"
            ),
            {
                "i": IDENTITY,
                "s": datetime(2026, 6, 5, 23, 15, tzinfo=UTC),
                "e": datetime(2026, 10, 9, 1, 45, tzinfo=UTC),
                "b": 12001,
                "a": "other",
                "u": NOW,
            },
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            insert(HoldoutUnlockRow).values(
                identity=IDENTITY,
                window_start=datetime(2026, 6, 5, 23, 15, tzinfo=UTC),
                window_end=datetime(2026, 10, 9, 1, 45, tzinfo=UTC),
                bars=12001,
                actor="other",
                unlocked_at=NOW,
            )
        )


def test_two_datasets_do_not_share_a_holdout(engine: Engine) -> None:
    """The refusal is per dataset: another genuinely different holdout is not blocked."""
    other = holdout_identity(
        dataset_id="mt5-XAUUSD-M15-2026-10-09", market="XAUUSD", fingerprint="b" * 64
    )
    ledger = _ledger(engine)

    ledger.record(identity=IDENTITY, start=NOW, end=NOW, size=1)
    ledger.record(identity=other, start=NOW, end=NOW, size=1)

    assert len(ledger.history()) == 2


def test_an_empty_identity_is_refused(engine: Engine) -> None:
    """Without an identity nothing tells two holdouts apart, so the rule protects nothing."""
    with pytest.raises(ValueError, match="needs an identity"):
        _ledger(engine).record(identity="  ", start=NOW, end=NOW, size=1)


def test_the_trace_is_append_only(engine: Engine) -> None:
    """An unlock that happened cannot be edited away, or the trail is worth nothing."""
    from sqlalchemy.exc import IntegrityError

    ledger = _ledger(engine)
    ledger.record(identity=IDENTITY, start=NOW, end=NOW, size=1)

    with pytest.raises(IntegrityError, match="append-only"), engine.begin() as connection:
        connection.execute(
            text("UPDATE holdout_unlocks SET actor = 'someone else' WHERE identity = :i"),
            {"i": IDENTITY},
        )
