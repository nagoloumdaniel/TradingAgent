"""Où en est la campagne de paper trading (cahier v3 §44, §53 ; Q-15 ; TASK-071).

À lancer chaque semaine pendant les trente jours de la campagne, sur la même base que
l'agent (voir `docs/operations/campagne-paper.md`) :

    uv run python scripts/paper_campaign.py
    uv run python scripts/paper_campaign.py --json
    uv run python scripts/paper_campaign.py --market XAUUSD
    uv run python scripts/paper_campaign.py --at 2026-11-06T12:00:00+00:00

Lecture seule : ce script ne crée, ne modifie et ne supprime **aucune** ligne. Il lit
`DATABASE_URL` comme `control/cli.py` (variable d'environnement prioritaire, sinon `.env`).
Code de sortie : 0 = lecture réussie (le verdict est dans le texte ou le JSON), 2 = base
non configurée ou injoignable.
"""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import Engine  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from tradingagent.config.errors import ConfigError  # noqa: E402
from tradingagent.config.settings import load_database_settings  # noqa: E402
from tradingagent.reporting.campaign import PLAN, progress, render  # noqa: E402
from tradingagent.storage.engine import create_database_engine  # noqa: E402


def _use_utf8_when_redirected() -> None:
    """A redirected Windows pipe defaults to a legacy code page, which cannot encode every
    French accent. The report asks for UTF-8 instead of crashing once it is logged."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and not stream.isatty():
            reconfigure(encoding="utf-8", errors="replace")


def _engine_from_environment() -> Engine:
    settings = load_database_settings(Path(".env"))
    return create_database_engine(settings.database_url.get_secret_value())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paper_campaign",
        description="État de la campagne de paper trading (Q-15, TASK-071). Lecture seule.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="sortie JSON sérialisable, pour un usage automatisé",
    )
    parser.add_argument("--market", default=None, help="ne mesurer qu'un marché (ex. XAUUSD)")
    parser.add_argument(
        "--at",
        default=None,
        help="instant UTC de référence au format ISO 8601 ; par défaut, maintenant",
    )
    return parser


def _parse_at(value: str) -> datetime:
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as error:
        raise SystemExit(f"--at n'est pas un horodatage ISO 8601 : {value!r}") from error
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise SystemExit("--at doit porter un fuseau, par exemple 2026-11-06T12:00:00+00:00")
    return moment


def main(
    argv: Sequence[str] | None = None,
    engine_factory: Callable[[], Engine] = _engine_from_environment,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    args = _parser().parse_args(argv)
    _use_utf8_when_redirected()
    at = _parse_at(args.at) if args.at else now()
    try:
        engine = engine_factory()
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    except SQLAlchemyError as error:
        print(f"base injoignable : {error}", file=sys.stderr)
        return 2
    try:
        state = progress(engine, at=at, market=args.market, plan=PLAN)
    except SQLAlchemyError as error:
        # An operator runs this every week for a month: a clear line beats a traceback.
        print(f"lecture impossible : {error}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    if args.json:
        print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(render(state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
