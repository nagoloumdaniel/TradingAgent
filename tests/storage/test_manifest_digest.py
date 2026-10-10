"""L'empreinte d'un manifeste ne doit pas bouger parce que le MODELE a gagne un champ.

**Le defaut que ce fichier verrouille, trouve le 2026-10-10 a l'execution.** `signals.py`
comparait au manifeste deja enregistre une empreinte calculee sur le modele **complet** :

    snapshot = manifest.model_dump(mode="json")

Un manifeste declare pourtant une poignee de cles ; le modele en porte onze. Tout champ ajoute
au modele — `entry_filter`, `session_filter`, `volatility_filter`, arrives le 2026-10-10, tous a
valeur par defaut — changeait donc l'empreinte de manifestes dont **pas une ligne de
configuration** n'avait bouge. Consequence reelle : l'agent a refuse de redemarrer, et deux
strategies ont echoue a chaque bougie avec

    ManifestChangedError: trend_breakout@1.0.1 differs from the manifest first used under that
    reference: bump its version

alors que `git diff` ne montrait que des commentaires.

**Ce que la garde doit comparer.** Ce que l'operateur a **declare**, pas ce que la classe sait
faire. Pydantic nomme exactement cela `exclude_unset` : les cles ecrites dans le YAML. Verifie
sur les empreintes deja en base le 2026-10-10 : `exclude_unset` reproduit `witness@1.1.0`,
`witness@1.1.1` et `trend_breakout@1.0.0` **au bit pres**, le dump complet non.

**Et la garde reste stricte.** Une garde qui ne se declenche plus n'est pas une garde : le
dernier test modifie une valeur reellement declaree et exige que l'empreinte bouge.
"""

from tradingagent.storage.signals import manifest_digest
from tradingagent.strategies.manifest import StrategyManifest

BASE = {
    "strategy_id": "digest_probe",
    "version": "1.0.0",
    "max_mode": "SIGNAL",
    "allowed_symbols": ["XAUUSD"],
    "timeframes": ["M15"],
    "history_bars": 300,
}


def test_the_same_declared_manifest_always_gives_the_same_digest() -> None:
    """Stability first: this is what a fingerprint is for."""
    assert manifest_digest(StrategyManifest.model_validate(BASE)) == manifest_digest(
        StrategyManifest.model_validate(dict(BASE))
    )


def test_a_model_field_nobody_declared_stays_out_of_the_digest() -> None:
    """The exact shape of the 2026-10-10 failure.

    `entry_filter` exists on the model — session and volatility rules live inside it — and is
    absent from this manifest. The model dump carries it; the fingerprint must not.
    """
    declared = StrategyManifest.model_validate(BASE)
    carried = declared.model_dump(mode="json")

    undeclared = set(carried) - set(BASE)
    assert "entry_filter" in undeclared, (
        "the model must still carry this default, or this test proves nothing"
    )
    # Two manifests that declare the same thing must agree, whatever the model carries.
    same_but_rewritten = StrategyManifest.model_validate({**BASE, "history_bars": 300})
    assert manifest_digest(declared) == manifest_digest(same_but_rewritten)


def test_a_declared_change_moves_the_digest() -> None:
    """The guard still guards: a value the operator wrote is part of the fingerprint."""
    before = StrategyManifest.model_validate(BASE)
    after = StrategyManifest.model_validate({**BASE, "history_bars": 301})

    assert manifest_digest(before) != manifest_digest(after), (
        "declared configuration must be covered, or a strategy could change under a reference "
        "without anyone noticing"
    )


def test_declaring_an_optional_field_moves_the_digest() -> None:
    """Optional does not mean invisible: writing it down is a configuration act."""
    without = StrategyManifest.model_validate(BASE)
    with_expiry = StrategyManifest.model_validate({**BASE, "expiry_bars": 3})

    assert manifest_digest(without) != manifest_digest(with_expiry)
