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


class _Captured(Exception):
    pass


def test_a_postgres_connection_cannot_hang_and_dead_links_are_noticed() -> None:
    """2026-10-20..28: "connection timeout expired", "server closed the connection".

    The pooler closes idle connections and a laptop loses its network. A connection attempt
    must give up quickly (the loop then backs off), TCP keepalives must notice a dead link
    before the next statement does, and a pooled connection must be tested before it is used.
    """
    from sqlalchemy import event

    engine = create_database_engine(
        "postgresql://user:password@localhost:5432/db"  # pragma: allowlist secret
    )
    seen: dict[str, object] = {}

    @event.listens_for(engine, "do_connect")
    def _capture(
        dialect: object, record: object, cargs: object, cparams: dict[str, object]
    ) -> None:
        seen.update(cparams)
        raise _Captured

    try:
        with pytest.raises(_Captured):
            engine.connect()
        assert 0 < int(str(seen["connect_timeout"])) <= 15
        assert seen["keepalives"] == 1
        assert 0 < int(str(seen["keepalives_idle"])) <= 60
        assert engine.pool._pre_ping is True
        assert 0 < engine.pool._recycle <= 1800
    finally:
        engine.dispose()


def test_sqlite_gets_no_network_options(tmp_path: Path) -> None:
    from sqlalchemy import event

    engine = create_database_engine(f"sqlite:///{tmp_path / 'y.db'}")
    seen: dict[str, object] = {}

    @event.listens_for(engine, "do_connect")
    def _capture(
        dialect: object, record: object, cargs: object, cparams: dict[str, object]
    ) -> None:
        seen.update(cparams)
        raise _Captured

    try:
        with pytest.raises(_Captured):
            engine.connect()
        assert "connect_timeout" not in seen
    finally:
        engine.dispose()
