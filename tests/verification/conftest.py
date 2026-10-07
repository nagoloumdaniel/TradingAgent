"""Fixtures written by the verifier, deliberately independent of the other suites.

The verification suite re-derives its own helpers so that a defect hidden by a shared
fixture cannot be hidden twice.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
LOGIN = 40123456


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    """A real migrated database (SQLite), built by the migrations like production is."""
    url = f"sqlite:///{tmp_path / 'verification.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()
