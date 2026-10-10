"""Le registre du jeu scelle, vu depuis la campagne : deux formules qui doivent rester une.

**Ce que ces tests verrouillent.** Le registre vit dans `storage` et le protocole dans
`research`, et `backtest`/`research` sont `OFFLINE_ONLY` : `research` ne peut donc pas importer
`storage.holdout.holdout_identity`. La formule d'identite est ecrite des deux cotes, et deux
formules qui divergent laisseraient passer exactement ce que la table existe pour empecher — une
deuxieme ouverture du meme holdout sous un nom different.

Le test d'egalite est donc le plus important du fichier : il rend la duplication visible au lieu
de la laisser deriver en silence.
"""

from datetime import UTC, datetime

import pytest

from tradingagent.research.campaign import holdout_identity_for
from tradingagent.research.protocol import SealedAccessError
from tradingagent.storage.holdout import holdout_identity


def test_both_sides_build_the_same_identity() -> None:
    """The one assertion that keeps the two formulas from drifting apart."""

    class _Dataset:
        dataset_id = "mt5-BTCUSD-M15-2026-10-09"
        fingerprint = "a" * 64

    assert holdout_identity_for(_Dataset(), "BTCUSD") == holdout_identity(  # type: ignore[arg-type]
        dataset_id="mt5-BTCUSD-M15-2026-10-09", market="BTCUSD", fingerprint="a" * 64
    )


def test_the_sealed_set_tells_the_ledger_before_handing_over_candles() -> None:
    """A refusal from the ledger propagates and no candle is ever returned.

    This is the property that makes the trace more than a journal: if the ledger refuses, the
    caller does not get the out-of-sample data at all.
    """
    from tradingagent.core.market import Candle
    from tradingagent.core.timeframe import Timeframe
    from tradingagent.research.protocol import SealedSet

    class _RefusingLedger:
        def __init__(self) -> None:
            self.seen: list[str] = []

        def record(self, *, identity: str, start: datetime, end: datetime, size: int) -> None:
            self.seen.append(identity)
            raise AssertionError("already opened")

    candle = Candle(Timeframe.M15, datetime(2026, 6, 5, 23, 15, tzinfo=UTC), 1.0, 1.0, 1.0, 1.0)
    ledger = _RefusingLedger()
    sealed = SealedSet([candle], "token", ledger=ledger, identity="probe")

    with pytest.raises(AssertionError, match="already opened"):
        sealed.unlock("token")

    assert ledger.seen == ["probe"]
    assert sealed.unlock_count == 0, "a refused opening is not an opening"


def test_a_valid_token_with_a_wrong_one_leaks_nothing() -> None:
    """The token check still comes first, ledger or no ledger."""
    from tradingagent.core.market import Candle
    from tradingagent.core.timeframe import Timeframe
    from tradingagent.research.protocol import SealedSet

    class _RecordingLedger:
        def __init__(self) -> None:
            self.calls = 0

        def record(self, **_kwargs: object) -> None:
            self.calls += 1

    candle = Candle(Timeframe.M15, datetime(2026, 6, 5, 23, 15, tzinfo=UTC), 1.0, 1.0, 1.0, 1.0)
    ledger = _RecordingLedger()
    sealed = SealedSet([candle], "right", ledger=ledger, identity="probe")

    with pytest.raises(SealedAccessError):
        sealed.unlock("wrong")

    assert ledger.calls == 0, "a wrong token must not even reach the ledger"
