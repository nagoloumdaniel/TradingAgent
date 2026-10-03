from typing import Any

from sqlalchemy import Engine, create_engine, event


def create_database_engine(url: str) -> Engine:
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        # SQLite ships with foreign keys disabled; WAL lets readers work during writes.
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(connection: Any, _record: Any) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine
