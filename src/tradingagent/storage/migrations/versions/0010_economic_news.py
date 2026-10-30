"""the economic calendar, the news readings and the daily brief

The agent traded blind to announcements: a signal could be taken ninety seconds before the payrolls
and stopped out by the whipsaw that follows, and nothing in the database said an announcement had
been due. This revision adds the four tables the news package writes:

* `economic_events` -- the calendar and the market-moving headlines, one row per announcement
  (unique `key`), figures kept as the text the source published.
* `news_analyses` -- one reading of one event per (event, figures): model or deterministic
  fallback, per market, insert-only so the reading's history around a release is kept.
* `news_briefs` -- the morning summary of a day.
* `news_fetches` -- what each refresh of each feed amounted to, so the dashboard can say what is
  stale instead of showing an old calendar as if it were current.

None of them is evidence the risk engine reads; they feed a pre-risk gate and the dashboard, so
they are ordinary tables (the calendar is rewritten when a source reschedules an event). On
PostgreSQL, row level security is enabled without a policy, like the other tables, so the public
web API serves nothing from them.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-30 16:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

TABLES = ("economic_events", "news_analyses", "news_briefs", "news_fetches")


def upgrade() -> None:
    op.create_table(
        "economic_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("impact", sa.String(length=8), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("country", sa.String(length=8), nullable=False),
        sa.Column("forecast", sa.String(length=32), nullable=True),
        sa.Column("previous", sa.String(length=32), nullable=True),
        sa.Column("actual", sa.String(length=32), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_economic_events")),
        sa.UniqueConstraint("key", name="uq_economic_events_key"),
    )
    with op.batch_alter_table("economic_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_economic_events_starts_at"), ["starts_at"])

    op.create_table(
        "news_analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_key", sa.String(length=32), nullable=False),
        sa.Column("input_hash", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("verdicts", sa.JSON(), nullable=False),
        sa.Column("analysed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_news_analyses")),
    )
    with op.batch_alter_table("news_analyses", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_news_analyses_event_key"), ["event_key"])

    op.create_table(
        "news_briefs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("markets", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_news_briefs")),
    )
    with op.batch_alter_table("news_briefs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_news_briefs_day"), ["day"])

    op.create_table(
        "news_fetches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("items", sa.Integer(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("retry_after", sa.Integer(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_news_fetches")),
    )
    with op.batch_alter_table("news_fetches", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_news_fetches_source"), ["source"])

    if op.get_bind().dialect.name == "postgresql":
        # Supabase serves the public schema over a web API; RLS without any policy denies it.
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Back to 0009. The calendar and the readings are re-fetched or recomputed; nothing here is
    evidence that cannot be rebuilt, so the tables simply go."""
    op.drop_table("news_fetches")
    op.drop_table("news_briefs")
    op.drop_table("news_analyses")
    op.drop_table("economic_events")
