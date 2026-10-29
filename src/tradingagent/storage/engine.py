from typing import Any

from sqlalchemy import Engine, create_engine, event, make_url

# A connection attempt that cannot succeed must fail in seconds, not hang: the loop backs off
# and tries again, which a hung thread cannot do.
CONNECT_TIMEOUT_SECONDS = 10
# TCP keepalives notice a link the pooler or the network dropped (laptop sleep, Wi-Fi change)
# before the next statement runs into it as "server closed the connection unexpectedly".
KEEPALIVE_IDLE_SECONDS = 30
KEEPALIVE_INTERVAL_SECONDS = 10
KEEPALIVE_COUNT = 3
# Supabase's pooler closes idle connections: do not keep one longer than it keeps them.
POOL_RECYCLE_SECONDS = 600


def create_database_engine(url: str) -> Engine:
    driven = _with_driver(url)
    if make_url(driven).get_backend_name() == "sqlite":
        engine = create_engine(driven, pool_pre_ping=True)
    else:
        engine = create_engine(
            driven,
            pool_pre_ping=True,
            pool_recycle=POOL_RECYCLE_SECONDS,
            connect_args={
                "connect_timeout": CONNECT_TIMEOUT_SECONDS,
                "keepalives": 1,
                "keepalives_idle": KEEPALIVE_IDLE_SECONDS,
                "keepalives_interval": KEEPALIVE_INTERVAL_SECONDS,
                "keepalives_count": KEEPALIVE_COUNT,
            },
        )
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
