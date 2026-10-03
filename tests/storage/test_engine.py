from pathlib import Path

import pytest

from tradingagent.storage.engine import create_database_engine


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:password@localhost:5432/db",  # pragma: allowlist secret
        "postgres://user:password@localhost:5432/db",  # pragma: allowlist secret
        "postgresql+psycopg://user:password@localhost:5432/db",  # pragma: allowlist secret
    ],
)
def test_postgres_urls_use_the_psycopg_driver(url: str) -> None:
    engine = create_database_engine(url)
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "psycopg"
    finally:
        engine.dispose()


def test_sqlite_url_is_left_as_is(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'x.db'}")
    try:
        assert engine.dialect.name == "sqlite"
    finally:
        engine.dispose()
