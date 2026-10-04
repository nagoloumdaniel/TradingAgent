"""pin the append-only trigger function's search_path

Supabase's security advisor flags functions whose search_path can be changed by the
caller (lint 0011). The function only raises, so an empty search_path is enough.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-04 19:30:00.000000
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER FUNCTION forbid_append_only_change() SET search_path = ''")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER FUNCTION forbid_append_only_change() RESET search_path")
