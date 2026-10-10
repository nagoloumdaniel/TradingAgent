"""one opening of a sealed holdout, and only one per dataset

The sealed out-of-sample set is the only evidence in this project that no selection has touched,
and reading it is what spends it. `protocol.SealedSet` has always counted its unlocks -- but in
memory. A fresh `split_dataset` hands out a fresh sealed set with the counter back at zero, so
the same holdout could be opened again and again and nothing recorded it. Verified on
2026-10-10: after opening BTCUSD's holdout to validate `vwap_pullback@1.3.0`, `audit_log` held
not one row mentioning it. An assertion nobody can check afterwards is not a guarantee.

So the trace becomes a table. `identity` carries the dataset it was cut from, and it is
**unique**: the rule that a holdout is spent once belongs to the database, the same way
idempotency keys and the append-only history tables do. A second opening of the same dataset is
refused by the constraint rather than by a caller remembering to check.

The table carries the same append-only guard as the other evidence tables: an unlock that
happened cannot be edited away, or the trail would be worth exactly as much as the discipline it
replaces. The guard is written out here rather than shared with `0005`, because a migration that
imports another migration's helpers breaks the day that helper changes.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-11 01:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

TABLE = "holdout_unlocks"
GUARD_FUNCTION = "forbid_append_only_change"
MESSAGE_SUFFIX = " is append-only"


def _sqlite_guard() -> None:
    for event in ("UPDATE", "DELETE"):
        op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_no_{event.lower()}")
        op.execute(
            f"CREATE TRIGGER {TABLE}_no_{event.lower()} BEFORE {event} ON {TABLE} "
            f"BEGIN SELECT RAISE(ABORT, '{TABLE}{MESSAGE_SUFFIX}'); END"
        )


def _postgres_guard() -> None:
    op.execute(
        f"CREATE OR REPLACE FUNCTION {GUARD_FUNCTION}() RETURNS trigger LANGUAGE plpgsql "
        f"SET search_path = '' AS $$ BEGIN RAISE EXCEPTION '%{MESSAGE_SUFFIX}', TG_TABLE_NAME; "
        "END $$"
    )
    op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_no_change ON {TABLE}")
    op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_no_truncate ON {TABLE}")
    op.execute(
        f"CREATE TRIGGER {TABLE}_no_change BEFORE UPDATE OR DELETE ON {TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {GUARD_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER {TABLE}_no_truncate BEFORE TRUNCATE ON {TABLE} "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {GUARD_FUNCTION}()"
    )
    # Supabase serves the public schema over a web API; RLS without any policy denies it.
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")


def _ensure_guard() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        _sqlite_guard()
        return
    if dialect == "postgresql":
        _postgres_guard()
        return
    raise NotImplementedError(f"append-only triggers not written for {dialect}")


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        # 160 characters: the identity is built by the caller and names a dataset, a market and
        # a fingerprint. Narrower would silently truncate, and a truncated identity is a
        # different holdout -- the one failure mode this table exists to prevent.
        sa.Column("identity", sa.String(length=160), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bars", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(length=80), nullable=False),
        sa.Column("unlocked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identity", name="uq_holdout_unlocks_identity"),
    )
    _ensure_guard()


def downgrade() -> None:
    """Back to 0008: the trace goes with the table.

    Dropping it loses the record of which holdouts were opened, and that record cannot be
    rebuilt from anything else -- the unlocks happened in the past and left no other mark. A
    downgrade means losing the evidence, which is precisely why the table is append-only on the
    way up.
    """
    op.drop_table(TABLE)
