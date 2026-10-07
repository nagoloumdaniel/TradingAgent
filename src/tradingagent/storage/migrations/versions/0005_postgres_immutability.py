"""re-assert the append-only immutability guards for every dialect

The prerequisite left by TASK-005 is met by `0001` (SQLite and PostgreSQL triggers) and
`0002` (the extra `halt_commands` history table), and `0003` pins the trigger function's
`search_path` for Supabase's security advisor. This revision does not add a table: it is a
repair pass that makes the guard idempotently re-creatable, so a database where a trigger
was dropped by hand, restored from a partial dump, or created before `0003` is brought back
to the exact state `0001`-`0003` describe.

It stays applicable on SQLite: the same guard is (re)created with the SQLite syntax the
tests rely on, so the migration chain `0001 -> 0005` remains runnable without PostgreSQL.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-07 12:00:00.000000
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# signal_events, executions, trades and audit_log come from 0001; halt_commands from 0002.
APPEND_ONLY_TABLES = ("signal_events", "executions", "trades", "audit_log", "halt_commands")
GUARD_FUNCTION = "forbid_append_only_change"
MESSAGE_SUFFIX = " is append-only"


def _sqlite_guard() -> None:
    for table in APPEND_ONLY_TABLES:
        for event in ("UPDATE", "DELETE"):
            # DROP then CREATE: `CREATE TRIGGER IF NOT EXISTS` would silently keep a
            # trigger whose body drifted, which is the failure this pass must repair.
            op.execute(f"DROP TRIGGER IF EXISTS {table}_no_{event.lower()}")
            op.execute(
                f"CREATE TRIGGER {table}_no_{event.lower()} BEFORE {event} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table}{MESSAGE_SUFFIX}'); END"
            )


def _postgres_guard() -> None:
    # The function only raises, so an empty search_path is enough and satisfies lint 0011.
    op.execute(
        f"CREATE OR REPLACE FUNCTION {GUARD_FUNCTION}() RETURNS trigger LANGUAGE plpgsql "
        f"SET search_path = '' AS $$ BEGIN RAISE EXCEPTION '%{MESSAGE_SUFFIX}', TG_TABLE_NAME; "
        "END $$"
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_change ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}")
        op.execute(
            f"CREATE TRIGGER {table}_no_change BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {GUARD_FUNCTION}()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {GUARD_FUNCTION}()"
        )
        # Supabase serves the public schema over a web API; RLS without any policy denies it.
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def _ensure_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        _sqlite_guard()
        return
    if dialect == "postgresql":
        _postgres_guard()
        return
    # Fail loudly rather than silently ship a database with mutable history.
    raise NotImplementedError(f"append-only triggers not written for {dialect}")


def upgrade() -> None:
    _ensure_guards()


def downgrade() -> None:
    # Nothing 0005 creates is new: it re-creates what 0001 and 0002 already declare. Undoing
    # it by dropping triggers would remove protection those revisions still require, so the
    # downgrade restores that same state. The `0001` and `0002` downgrades remove the
    # triggers and the function before the tables they protect are dropped.
    _ensure_guards()
