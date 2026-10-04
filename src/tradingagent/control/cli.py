"""Server-side emergency stop, independent of Telegram (TASK-036, action 4).

    uv run tradingagent status
    uv run tradingagent halt --reason "news risk" [--close-positions]
    uv run tradingagent resume --reason "checked the account"
    uv run tradingagent rearm --strategy witness@1.0.0 --symbol XAUUSD

It writes straight to the database, so it works while the agent or the bot is down.
"""

import argparse
import getpass
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine

from tradingagent.config.errors import ConfigError
from tradingagent.config.settings import load_database_settings
from tradingagent.core.halt import GLOBAL, pair_scope
from tradingagent.core.states import HaltAction, HaltSource
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.halts import HaltCommand, HaltStore


def _engine_from_environment() -> Engine:
    settings = load_database_settings(Path(".env"))
    return create_database_engine(settings.database_url.get_secret_value())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tradingagent", description="Emergency stop control")
    commands = parser.add_subparsers(dest="command", required=True)
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


def main(
    argv: Sequence[str] | None = None,
    engine_factory: Callable[[], Engine] = _engine_from_environment,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    args = _parser().parse_args(argv)
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
