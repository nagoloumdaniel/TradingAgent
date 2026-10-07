"""Database built by the migrations, so the append-only triggers exist (as in storage)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'runtime.db'}"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    upgrade(database_url)
    built = create_database_engine(database_url)
    yield built
    built.dispose()
