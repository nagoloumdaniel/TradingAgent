"""Operator surface for the versioned registry (cahier v3 §14, §30, §49).

    uv run python -m tradingagent.registry.cli registry-status [--market XAUUSD]
    uv run python -m tradingagent.registry.cli registry-validate \\
        --market XAUUSD --ref witness@1.1.0 --stage risk --detail '{"max_drawdown": 0.2}'
    uv run python -m tradingagent.registry.cli registry-promote \\
        --market XAUUSD --ref witness@1.1.0 --reason "nine gates cleared"

Like `control/cli.py`, it writes straight to the database through the engine factory, so it
works while the agent or the bot is down. Every command takes `--actor`, defaulting to the
operating system user, because a transition without a name is not auditable.
"""

import argparse
import getpass
import json
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine

from tradingagent.config.errors import ConfigError
from tradingagent.config.settings import load_database_settings
from tradingagent.core.states import ValidationStage
from tradingagent.registry.store import RegistryError, StrategyRegistry
from tradingagent.storage.engine import create_database_engine


def _engine_from_environment() -> Engine:
    settings = load_database_settings(Path(".env"))
    return create_database_engine(settings.database_url.get_secret_value())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tradingagent.registry.cli",
        description="Versioned strategy registry",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("registry-status", help="list the registry, one ref per row")
    status.add_argument("--market", help="restrict the listing to one market")
    status.add_argument("--actor", default=getpass.getuser())

    validate = commands.add_parser("registry-validate", help="record one gate verdict")
    validate.add_argument("--market", required=True)
    validate.add_argument("--ref", required=True, help="reference, e.g. witness@1.1.0")
    validate.add_argument("--stage", required=True, help="a stage of the §49 protocol")
    validate.add_argument("--fail", action="store_true", help="record a failed gate")
    validate.add_argument("--detail", default="{}", help="JSON object with the evidence")
    validate.add_argument("--actor", default=getpass.getuser())

    promote = commands.add_parser("registry-promote", help="send a candidate to LIVE")
    promote.add_argument("--market", required=True)
    promote.add_argument("--ref", required=True, help="reference, e.g. witness@1.1.0")
    promote.add_argument("--reason", required=True)
    promote.add_argument("--actor", default=getpass.getuser())
    return parser


def main(
    argv: Sequence[str] | None = None,
    engine_factory: Callable[[], Engine] = _engine_from_environment,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    args = _parser().parse_args(argv)
    try:
        engine = engine_factory()
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    try:
        store = StrategyRegistry(engine, clock=clock)
        if args.command == "registry-status":
            _print_status(store, args.market)
            return 0
        if args.command == "registry-validate":
            stage = _stage(args.stage)
            if stage is None:
                return 2
            detail = _detail(args.detail)
            if detail is None:
                return 2
            try:
                store.record_validation(
                    args.ref, args.market, stage, not args.fail, detail, clock()
                )
            except (RegistryError, ValueError) as error:
                print(error, file=sys.stderr)
                return 1
            verdict = "failed" if args.fail else "passed"
            print(f"RECORDED: {stage.value} {verdict} for {args.ref} on {args.market}")
            return 0
        try:
            store.promote(args.market, args.ref, args.actor, args.reason, clock())
        except (RegistryError, ValueError) as error:
            print(error, file=sys.stderr)
            return 1
        print(f"PROMOTED: {args.ref} is LIVE on {args.market} ({args.actor})")
        return 0
    finally:
        engine.dispose()


def _stage(value: str) -> ValidationStage | None:
    try:
        return ValidationStage(value)
    except ValueError:
        known = ", ".join(stage.value for stage in ValidationStage)
        print(f"unknown validation stage {value!r}; expected one of: {known}", file=sys.stderr)
        return None


def _detail(document: str) -> dict[str, object] | None:
    try:
        parsed = json.loads(document)
    except json.JSONDecodeError as error:
        print(f"--detail is not valid JSON: {error}", file=sys.stderr)
        return None
    if not isinstance(parsed, dict):
        print("--detail must be a JSON object", file=sys.stderr)
        return None
    return parsed


def _print_status(store: StrategyRegistry, market: str | None) -> None:
    markets = [market] if market else store.markets()
    histories = [
        store.history(row.ref, one_market)
        for one_market in markets
        for row in store.list_market(one_market)
    ]
    if not histories:
        print("REGISTRY: no strategy registered")
        return
    print(f"{'MARKET':<10} {'REF':<26} {'STATUS':<12} {'LIVE':<6} UPDATED")
    for history in histories:
        live = "yes" if history.status.value == "live" else "no"
        print(
            f"{history.market:<10} {history.ref:<26} {history.status.value:<12} {live:<6} "
            f"{history.updated_at.isoformat()}"
        )


if __name__ == "__main__":
    sys.exit(main())
