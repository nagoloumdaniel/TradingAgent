"""Remonte une version de strategie de DISCOVERED jusqu'a PAPER, en journalisant chaque pas.

**Pourquoi ce script, et pourquoi il n'est pas un raccourci.** `config/agent.yaml` designe une
version ; le registre dit ce qu'elle a le droit de faire. Une version fraichement referencee est
`discovered`, et un mode qui execute (PAPER, DEMO, LIVE) exige au moins `paper` : l'agent refuse
de demarrer tant que la version n'a pas gravi les echelons. C'est la garde du §14, et elle doit
rester : elle empeche qu'une reference ecrite dans un fichier devienne executante par accident.

Ce script ne contourne pas la garde, il **parcourt** l'echelle autorisee — `discovered` ->
`experimental` -> `backtesting` -> `validating` -> `paper` — en passant par la seule API
publique du registre, qui verifie chaque transition et l'audite avec son auteur et son motif.

**Ce qu'il ne fait pas.** Il ne promeut pas en `candidate` ni en `live` : ces deux-la engagent
des ordres, et `promote()` exige les neuf portes du §49. Le script s'arrete a `paper`, qui est le
minimum pour un compte de demonstration, et c'est exactement ce que la derogation du manifeste
autorise.

    uv run python scripts/registry/climb_to_paper.py --ref vwap_pullback@1.1.0 --market XAUUSD
    uv run python scripts/registry/climb_to_paper.py --from-agent-config --dry-run

Le motif ecrit dans la piste d'audit reprend la decision de l'operateur : c'est ce qui distingue
« une version a ete autorisee a s'executer » de « quelqu'un a change un statut ».
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from tradingagent.core.states import StrategyStatus  # noqa: E402
from tradingagent.registry.store import StrategyRegistry, UnknownStrategyRef  # noqa: E402
from tradingagent.storage.engine import create_database_engine  # noqa: E402

#: Les echelons a gravir, dans l'ordre impose par `ALLOWED_STATUS_TRANSITIONS`. S'arreter a
#: `paper` est une decision : c'est le minimum qu'un mode executant accepte, et la suite engage
#: des ordres.
LADDER: tuple[StrategyStatus, ...] = (
    StrategyStatus.EXPERIMENTAL,
    StrategyStatus.BACKTESTING,
    StrategyStatus.VALIDATING,
    StrategyStatus.PAPER,
)

#: Le point de depart normal d'une version fraichement referencee. Il figure dans le chemin
#: parcouru — sans lui, `climb` refuserait exactement le cas pour lequel il existe — mais pas
#: dans les cibles : on ne « monte » jamais vers `discovered`.
START = StrategyStatus.DISCOVERED

REASON = (
    "decision de l'operateur du 2026-10-10 : vwap_pullback remplace witness et trend_breakout "
    "sur les deux marches, en demonstration, apres mesure (PF net 0,68 BTC et 0,55 or en "
    "validation, contre 1,20 exige). Aucune porte du §49 n'est franchie : le plafond DEMO est "
    "une derogation, pas une validation."
)
ACTOR = "operator-decision/2026-10-10"


def _deployed(agent_config: Path) -> list[tuple[str, str]]:
    """What `agent.yaml` designates, enabled markets only."""
    import yaml

    document = yaml.safe_load(agent_config.read_text(encoding="utf-8"))
    found: list[tuple[str, str]] = []
    for market in document.get("markets", []):
        if market.get("enabled", True):
            found.append((str(market["symbol"]), str(market["strategy"])))
    return found


def climb(registry: StrategyRegistry, market: str, ref: str, at: datetime, *, dry_run: bool) -> int:
    try:
        row = registry.get(market, ref)
    except UnknownStrategyRef:
        print(f"  {ref} sur {market} : inconnue du registre, rien a faire")
        return 1
    print(f"  {ref} sur {market} : {row.status.value}")
    if row.status is StrategyStatus.PAPER:
        print("    deja a paper, aucun changement")
        return 0
    if row.status is StrategyStatus.DEPRECATED:
        print("    DEPRECATED : la remonter serait effacer une decision. Refus.")
        return 1
    index = [START, *LADDER]
    if row.status not in index:
        print(f"    statut {row.status.value} hors de l'echelle geree par ce script. Refus.")
        return 1
    current = row.status
    for target in index[index.index(current) + 1 :]:
        if dry_run:
            print(f"    [essai] {current.value} -> {target.value}")
        else:
            registry.transition(market, ref, target, ACTOR, REASON, at)
            print(f"    {current.value} -> {target.value} (audite, auteur {ACTOR})")
        current = target
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default=None, help="ex. vwap_pullback@1.1.0")
    parser.add_argument("--market", default=None, help="ex. XAUUSD")
    parser.add_argument(
        "--from-agent-config",
        action="store_true",
        help="remonter tout ce que config/agent.yaml designe",
    )
    parser.add_argument("--agent-config", type=Path, default=ROOT / "config" / "agent.yaml")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.from_agent_config:
        targets = _deployed(args.agent_config)
    elif args.ref and args.market:
        targets = [(args.market, args.ref)]
    else:
        parser.error("donne --ref et --market, ou --from-agent-config")

    import os

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL est absent de l'environnement")

    engine = create_database_engine(url)
    at = datetime.now(UTC)
    failures = 0
    try:
        registry = StrategyRegistry(engine)
        print(f"echelle : {' -> '.join(status.value for status in LADDER)}")
        for market, ref in targets:
            failures += climb(registry, market, ref, at, dry_run=args.dry_run)
    finally:
        engine.dispose()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
