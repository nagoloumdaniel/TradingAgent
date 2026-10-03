from typing import Any

from sqlalchemy import Engine, create_engine, event, make_url


def create_database_engine(url: str) -> Engine:
    engine = create_engine(_with_driver(url), pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        # SQLite ships with foreign keys disabled; WAL lets readers work during writes.
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(connection: Any, _record: Any) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


def _with_driver(url: str) -> str:
    """Hosting dashboards hand out postgres:// or postgresql://; pin the psycopg 3 driver."""
    parsed = make_url(
        url.replace("postgres://", "postgresql://", 1) if url.startswith("postgres://") else url
    )
    if parsed.drivername == "postgresql":
        parsed = parsed.set(drivername="postgresql+psycopg")
    return parsed.render_as_string(hide_password=False)
