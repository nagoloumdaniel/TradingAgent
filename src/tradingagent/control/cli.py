"""Server-side emergency stop, independent of Telegram (TASK-036, action 4).

    uv run tradingagent doctor
    uv run tradingagent status
    uv run tradingagent halt --reason "news risk" [--close-positions]
    uv run tradingagent resume --reason "checked the account"
    uv run tradingagent rearm --strategy witness@1.0.0 --symbol XAUUSD

It writes straight to the database, so it works while the agent or the bot is down.
`doctor` is the exception: it answers on a configuration that is not yet valid, which is
precisely when it is useful.
"""

import argparse
import getpass
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, text

from tradingagent.config.doctor import diagnose, read_env, render
from tradingagent.config.errors import ConfigError
from tradingagent.config.settings import load_database_settings
from tradingagent.core.halt import GLOBAL, pair_scope
from tradingagent.core.states import HaltAction, HaltSource
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.halts import HaltCommand, HaltStore
from tradingagent.storage.migrate import head_revision

ENV_FILE = Path(".env")


def _use_utf8_console() -> None:
    """A redirected Windows pipe defaults to a legacy code page, which mangles every
    French accent in the report. Ask for UTF-8 instead of printing mojibake."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and not stream.isatty():
            reconfigure(encoding="utf-8", errors="replace")


def _engine_from_environment() -> Engine:
    settings = load_database_settings(ENV_FILE)
    return create_database_engine(settings.database_url.get_secret_value())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tradingagent", description="Emergency stop control")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "doctor",
        help="état des clés de configuration, et où trouver celles qui manquent",
    )
    commands.add_parser("status", help="show whether trading is halted, and why")
    halt = commands.add_parser("halt", help="suspend every new order")
    halt.add_argument("--reason", required=True)
    halt.add_argument(
        "--close-positions",
        action="store_true",
        help="also close open positions (never implied)",
    )
    resume = commands.add_parser("resume", help="lift the global halt")
    resume.add_argument("--reason", required=True)
    rearm = commands.add_parser("rearm", help="lift a strategy's quarantine on one market")
    rearm.add_argument("--strategy", required=True, help="reference, e.g. witness@1.0.0")
    rearm.add_argument("--symbol", required=True)
    for sub in (halt, resume, rearm):
        sub.add_argument("--actor", default=getpass.getuser())
    return parser


def _doctor(env_path: Path, engine_factory: Callable[[], Engine]) -> int:
    """Report the configuration, then probe the two things it can actually reach.

    Every probe is best-effort: this command exists to be run *before* the configuration is
    correct, so nothing here may raise.
    """
    diagnosis = diagnose(env_path)
    print(render(diagnosis))
    print()
    print("Connexions :")
    try:
        engine = engine_factory()
    except ConfigError as error:
        print(f"  base         : non configurée ({error})")
    except Exception as error:  # a wrong password or a dead network is a diagnosis too
        print(f"  base         : injoignable ({error.__class__.__name__})")
    else:
        try:
            with engine.connect() as connection:
                dialect = connection.dialect.name
                connection.execute(text("SELECT 1"))
            print(f"  base         : joignable ({dialect})")
            _report_schema(engine)
        except Exception as error:
            print(f"  base         : erreur à la connexion ({error.__class__.__name__})")
        finally:
            engine.dispose()

    raw = read_env(env_path).get("EA_FILES_DIR", "").strip()
    if not raw:
        print("  répertoire EA: non configuré — l'agent tournera sans EA, état valide")
    elif Path(raw).is_dir():
        print(f"  répertoire EA: présent ({raw})")
    else:
        print(f"  répertoire EA: ABSENT, il sera créé au premier échange ({raw})")

    print()
    if diagnosis.ok:
        print("Configuration complète pour démarrer l'agent.")
        return 0
    print(
        f"{len(diagnosis.blocked)} clé(s) obligatoire(s) manquante(s) : "
        "l'agent ne peut pas démarrer."
    )
    return 1


def _report_schema(engine: Engine) -> None:
    """Compare the database's revision with the one the code ships.

    A schema left behind by an older checkout is the silent failure this catches: every
    query works until the one that needs the new column.
    """
    try:
        with engine.connect() as connection:
            applied = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:
        print(
            "  schéma       : inconnu (table alembic_version absente : migrations non appliquées)"
        )
        return
    expected = head_revision()
    if str(applied) == expected:
        print(f"  schéma       : à jour ({expected})")
    else:
        print(
            f"  schéma       : EN RETARD — base en {applied}, code en {expected} "
            "(lancer `uv run tradingagent-run`, qui migre au démarrage)"
        )


def main(
    argv: Sequence[str] | None = None,
    engine_factory: Callable[[], Engine] = _engine_from_environment,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    env_path: Path = ENV_FILE,
) -> int:
    args = _parser().parse_args(argv)
    _use_utf8_console()
    if args.command == "doctor":
        return _doctor(env_path, engine_factory)
    try:
        engine = engine_factory()
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    try:
        store = HaltStore(engine)
        if args.command == "halt":
            store.issue(
                HaltCommand(
                    GLOBAL,
                    HaltAction.HALT,
                    HaltSource.SERVER,
                    args.reason,
                    args.actor,
                    now(),
                    args.close_positions,
                )
            )
        elif args.command == "resume":
            store.issue(
                HaltCommand(
                    GLOBAL, HaltAction.RESUME, HaltSource.SERVER, args.reason, args.actor, now()
                )
            )
        elif args.command == "rearm":
            store.issue(
                HaltCommand(
                    pair_scope(args.strategy, args.symbol),
                    HaltAction.RESUME,
                    HaltSource.SERVER,
                    "re-armed by the operator",
                    args.actor,
                    now(),
                )
            )
        _print_status(store)
        return 0
    finally:
        engine.dispose()


def _print_status(store: HaltStore) -> None:
    status = store.status()
    if not status.halted:
        print("TRADING: no halt active")
    else:
        print("HALTED" + (" (closing open positions)" if status.close_positions else ""))
        for reason in status.reasons:
            print(f"  {reason}")
    try:
        pairs = store.halted_pairs()
    except Exception as error:
        print(f"QUARANTINES UNREADABLE: {error.__class__.__name__}")
        return
    for ref, symbol in sorted(pairs):
        print(f"QUARANTINED: {ref} on {symbol}")


if __name__ == "__main__":
    sys.exit(main())
