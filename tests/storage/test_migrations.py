import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text

from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import downgrade, upgrade
from tradingagent.storage.models import Base

TABLES = {
    "candles",
    "strategy_versions",
    "signals",
    "signal_events",
    "ai_calls",
    "risk_decisions",
    "orders",
    "executions",
    "positions",
    "trades",
    "reports",
    "system_events",
    "audit_log",
}


def tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


def test_upgrade_creates_every_table(engine: Engine) -> None:
    assert tables(engine) == TABLES


def test_models_declare_exactly_the_migrated_tables() -> None:
    assert set(Base.metadata.tables) == TABLES


def test_migration_replays_on_an_empty_database(database_url: str) -> None:
    upgrade(database_url)
    downgrade(database_url)
    engine = create_database_engine(database_url)
    try:
        assert tables(engine) == set()
    finally:
        engine.dispose()
    upgrade(database_url)
    engine = create_database_engine(database_url)
    try:
        assert tables(engine) == TABLES
    finally:
        engine.dispose()


def test_models_and_migration_do_not_drift(engine: Engine) -> None:
    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert differences == []


def test_foreign_keys_are_enforced(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        pytest.skip("only SQLite can disable foreign keys")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_row_level_security_hides_every_table_from_the_supabase_api(engine: Engine) -> None:
    # Supabase serves the public schema over a web API: without RLS, its public key reads it.
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL only")
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT relname, relrowsecurity FROM pg_class "
                "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
            )
        ).all()
    protected = {name for name, enabled in rows if enabled}
    assert protected >= TABLES | {"alembic_version"}
