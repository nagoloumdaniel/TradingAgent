"""the market dimension of reports and system events

The operator reads the dashboard one market at a time. Two tables could not be separated:
`reports` held a period, a window and the text that was generated, with no column saying
which instrument the report is about, and `system_events` buried the symbol inside its
free-form `detail` JSON, when it carried one at all. Both new columns are nullable on
purpose: a report covering two markets has no single market, and an account-wide event has
no symbol. A migration that invented one would be worse than an empty cell.

The backfill writes only what the data itself already says:

* `reports.market` is filled when the window holds signals from exactly one market — the
  same rule `ReportGenerator.sole_market` applies at write time, so a backfilled report and
  a freshly written one are attributed identically. A window with no signal or with two
  markets keeps NULL: the report is a global one, and its own text still names the markets
  that were active.
* `system_events.symbol` is read from the event's own `detail["symbol"]` — the value the
  writer recorded (strategy errors, skipped evaluations, offline Guardians). Every other
  kind (clock mismatch, AI lab failure, live activation, mode command) has no symbol and
  stays NULL.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-08 02:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

SYMBOL_LENGTH = 32
REPORT_INDEX = "ix_reports_market"
EVENT_INDEX = "ix_system_events_symbol"


def _backfill_report_markets() -> None:
    """Attribute each report to the market its window holds — only when there is one.

    Without `GROUP BY`, the aggregate subquery yields exactly one row and the `HAVING`
    removes it when the window holds zero or several markets: both cases end as NULL, for
    the same reason. The window is half-open, `[window_start, window_end)`, exactly as the
    report arithmetic defines it.
    """
    op.execute(
        "UPDATE reports SET market = ("
        " SELECT MIN(s.symbol) FROM signals s"
        " WHERE s.generated_at >= reports.window_start"
        "   AND s.generated_at < reports.window_end"
        " HAVING COUNT(DISTINCT s.symbol) = 1"
        ")"
    )


def _backfill_event_symbols() -> None:
    """Copy `detail["symbol"]` into the column, on both dialects, and only if it is text.

    A row whose detail carries no symbol, or carries something that is not a string, is
    left alone: the column must never hold a value the event did not state.
    """
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            "UPDATE system_events SET symbol = json_extract(detail, '$.symbol') "
            "WHERE json_valid(detail) AND json_type(detail, '$.symbol') = 'text'"
        )
        return
    if dialect == "postgresql":
        op.execute(
            "UPDATE system_events SET symbol = detail ->> 'symbol' "
            "WHERE json_typeof(detail) = 'object' "
            "AND json_typeof(detail -> 'symbol') = 'string'"
        )
        return
    raise NotImplementedError(f"symbol backfill not written for {dialect}")


def upgrade() -> None:
    with op.batch_alter_table("reports", schema=None) as batch_op:
        batch_op.add_column(sa.Column("market", sa.String(length=SYMBOL_LENGTH), nullable=True))
    with op.batch_alter_table("system_events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("symbol", sa.String(length=SYMBOL_LENGTH), nullable=True))

    _backfill_report_markets()
    _backfill_event_symbols()

    # Both columns are filter criteria on their page: without an index, every filtered
    # view would scan the whole history.
    with op.batch_alter_table("reports", schema=None) as batch_op:
        batch_op.create_index(batch_op.f(REPORT_INDEX), ["market"], unique=False)
    with op.batch_alter_table("system_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f(EVENT_INDEX), ["symbol"], unique=False)


def downgrade() -> None:
    """Back to 0006 exactly: the two indexes and the two columns, nothing else.

    The data the columns held is gone with them, which is what a downgrade means here —
    the value is derivable from `signals` and from `detail`, and `upgrade` derives it again.
    """
    with op.batch_alter_table("reports", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f(REPORT_INDEX))
        batch_op.drop_column("market")
    with op.batch_alter_table("system_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f(EVENT_INDEX))
        batch_op.drop_column("symbol")
