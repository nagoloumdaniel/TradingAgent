"""halt commands

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04 18:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

import tradingagent.storage.types

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLE = "halt_commands"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=128), nullable=False),
        sa.Column(
            "action",
            sa.Enum(
                "halt",
                "resume",
                name="haltaction",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("close_positions", sa.Boolean(), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "automatic",
                "telegram",
                "server",
                name="haltsource",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", tradingagent.storage.types.UtcDateTime(), nullable=False),
        sa.CheckConstraint(
            "action IN ('halt', 'resume')", name=op.f("ck_halt_commands_haltaction")
        ),
        sa.CheckConstraint(
            "source IN ('automatic', 'telegram', 'server')",
            name=op.f("ck_halt_commands_haltsource"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_halt_commands")),
    )
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_halt_commands_scope"), ["scope"], unique=False)

    # Append-only like the other history tables: a halt is lifted by a newer command,
    # never by editing or deleting the one that set it.
    if op.get_bind().dialect.name == "sqlite":
        for event in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER {TABLE}_no_{event.lower()} BEFORE {event} ON {TABLE} "
                f"BEGIN SELECT RAISE(ABORT, '{TABLE} is append-only'); END"
            )
        return
    op.execute(
        f"CREATE TRIGGER {TABLE}_no_change BEFORE UPDATE OR DELETE ON {TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION forbid_append_only_change()"
    )
    op.execute(
        f"CREATE TRIGGER {TABLE}_no_truncate BEFORE TRUNCATE ON {TABLE} "
        "FOR EACH STATEMENT EXECUTE FUNCTION forbid_append_only_change()"
    )
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        for event in ("update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_no_{event}")
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_no_change ON {TABLE}")
        op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_no_truncate ON {TABLE}")
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_halt_commands_scope"))
    op.drop_table(TABLE)
