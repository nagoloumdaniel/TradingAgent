import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from tests._database_guard import guard_test_database
from tests.conftest import env_value, shared_server

from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import downgrade, upgrade

# Set to run the storage tests against a real server. Every test drops all tables.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
# What the agent itself uses, so the guard can prove the two are different. Read from the
# operator's file rather than exported into the process: the rest of the session has no
# business seeing the production credentials.
PRODUCTION_DATABASE_URL = env_value("DATABASE_URL")


def _guard(url: str) -> str:
    try:
        return guard_test_database(url, PRODUCTION_DATABASE_URL)
    except ValueError as error:
        raise pytest.UsageError(str(error)) from error


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    url = shared_server()
    if url:
        return url
    return f"sqlite:///{tmp_path / 'agent.db'}"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    """A database built by the migrations, not by create_all: triggers exist only there."""
    on_server = database_url.startswith("postgresql")
    if on_server:
        downgrade(database_url)  # start clean whatever the previous test left
    upgrade(database_url)
    built = create_database_engine(database_url)
    yield built
    built.dispose()
    if on_server:
        downgrade(database_url)
