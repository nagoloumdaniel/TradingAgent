"""A migrated database for the registry tests.

The registry writes to `strategy_registry`, `validation_runs`, `backtest_runs` and
`audit_log`: all four exist only after the Alembic migrations, never after `create_all`.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, make_url

from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import downgrade, upgrade

# Set to run the registry tests against a real server. Every test drops all tables.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _guard(url: str) -> str:
    database = make_url(url).database or ""
    if not database.endswith("_test"):
        raise pytest.UsageError(
            f"TEST_DATABASE_URL must name a database ending in _test, got {database!r}: "
            "these tests drop every table"
        )
    return url


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    if TEST_DATABASE_URL:
        return _guard(TEST_DATABASE_URL)
    return f"sqlite:///{tmp_path / 'registry.db'}"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    shared_server = TEST_DATABASE_URL is not None
    if shared_server:
        downgrade(database_url)  # start clean whatever the previous test left
    upgrade(database_url)
    built = create_database_engine(database_url)
    yield built
    built.dispose()
    if shared_server:
        downgrade(database_url)
