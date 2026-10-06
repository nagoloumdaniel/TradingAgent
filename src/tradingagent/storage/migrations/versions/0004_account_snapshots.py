"""account snapshots

Periodic equity records, the anchors of the daily report's opening and closing balances
(section 14.3) and of the equity curve (TASK-043).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-07 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

import tradingagent.storage.types

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TABLE = "account_snapshots"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("equity", tradingagent.storage.types.ExactDecimal(length=48), nullable=False),
        sa.Column("balance", tradingagent.storage.types.ExactDecimal(length=48), nullable=False),
        sa.Column("at", tradingagent.storage.types.UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_snapshots")),
    )
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_account_snapshots_at"), ["at"], unique=False)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE account_snapshots ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table(TABLE)
