from collections.abc import Sequence

from sqlalchemy import Engine, Insert
from sqlalchemy.dialects import postgresql, sqlite

from tradingagent.storage.models import Base


def insert_ignoring_duplicates(
    engine: Engine, table: type[Base], unique_key: Sequence[str]
) -> Insert:
    """INSERT that lets the unique constraint drop duplicates: safe under races and retries."""
    dialect = engine.dialect.name
    if dialect == "postgresql":
        return postgresql.insert(table).on_conflict_do_nothing(index_elements=unique_key)
    if dialect == "sqlite":
        return sqlite.insert(table).on_conflict_do_nothing(index_elements=unique_key)
    raise NotImplementedError(f"no duplicate-safe insert for {dialect}")
