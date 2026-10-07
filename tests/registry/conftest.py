"""A migrated database for the registry tests.

The registry writes to `strategy_registry`, `validation_runs`, `backtest_runs` and
`audit_log`: all four exist only after the Alembic migrations, never after `create_all`.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from tests.conftest import shared_server

from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import downgrade, upgrade


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    url = shared_server()
    if url:
        return url
    return f"sqlite:///{tmp_path / 'registry.db'}"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    shared = database_url.startswith("postgresql")
    if shared:
        downgrade(database_url)  # start clean whatever the previous test left
    upgrade(database_url)
    built = create_database_engine(database_url)
    yield built
    built.dispose()
    if shared:
        downgrade(database_url)
