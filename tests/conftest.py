"""Session-wide test configuration.

`.env` is where this project keeps every setting, but pytest only ever sees `os.environ`.
Without this file `TEST_DATABASE_URL` — the one variable the suite reads from the file — is
silently ignored, and the PostgreSQL tests skip as though it were absent. That is exactly
how the migration defect of `0006` reached a green suite.

**Only that variable is exported.** An earlier version loaded the whole file, which put the
operator's credentials into `os.environ` for every test and broke the ones that deliberately
build a minimal configuration to check that a missing key is refused. A test session must not
inherit a developer's `.env`.

Loaded at import time, not in a fixture: the package conftests read it at module level, and
the root conftest is imported first.
"""

import os
from pathlib import Path

import pytest
from tests._database_guard import guard_test_database

from tradingagent.config.doctor import read_env

ROOT = Path(__file__).resolve().parents[1]

# The only key the suite takes from the file. Everything else a test needs, it builds.
NEEDED = ("TEST_DATABASE_URL",)


def env_value(name: str) -> str | None:
    """A setting, from the environment first and from `.env` second.

    Used where a test needs a value out of the operator's file without exporting the whole
    thing into the process: the safety guard compares the test URL with the production one.
    """
    from_environment = os.environ.get(name)
    if from_environment:
        return from_environment
    return read_env(ROOT / ".env").get(name) or None


def _export_needed() -> None:
    values = read_env(ROOT / ".env")
    for name in NEEDED:
        value = values.get(name, "").strip()
        if value and not os.environ.get(name):
            os.environ[name] = value


_export_needed()


def shared_server() -> str | None:
    """The remote database engine-agnostic suites may use, or ``None`` for SQLite.

    Two conditions, and the second is the one that matters: `TEST_DATABASE_URL` says a server
    exists, and `TRADINGAGENT_TEST_ON_SERVER` says this run is willing to pay for it. Every
    test using the shared server migrates the whole schema before it starts — about four
    seconds over the network — so a suite that always took it turned a three-minute run into
    a thirteen-minute one.

    The PostgreSQL-specific module (`tests/storage/test_postgres_immutability.py`) does not
    go through here: it is module-scoped, migrates once, and is the evidence that actually
    needs a real server.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not os.environ.get("TRADINGAGENT_TEST_ON_SERVER"):
        return None
    try:
        return guard_test_database(url, env_value("DATABASE_URL"))
    except ValueError as error:
        raise pytest.UsageError(str(error)) from error
