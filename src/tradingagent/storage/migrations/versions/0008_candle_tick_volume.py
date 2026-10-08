"""the tick volume of a stored candle

`candles` kept the four prices and dropped the volume. The broker has always sent one --
`mt5_terminal.rates()` read `tick_volume` and never passed it on -- so the agent collected it
and lost it on write. Nothing needed it until a session VWAP existed, and a VWAP is a mean
weighted by volume: without this column, a production VWAP reads NULL everywhere and is
blind, while the same indicator works on a live stream and on the frozen datasets.

The column is nullable and stays null for every existing row. That is not a gap to fill in:
the volume of a bar that was written before the column existed was **never recorded**, and
backfilling it with zero would turn "unknown" into a measurement of "nothing traded" -- the
exact confusion the reading rules of `indicators/vwap.py` were built to avoid.

A migration cannot invent a value it never observed. So this one adds a column and stops
there, and the distinction between NULL and 0.0 is what makes the column usable.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-08 21:20:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("candles", schema=None) as batch_op:
        # `Double`, not `Float`: the four candle prices are already `Double` in 0001, and the
        # drift check (`test_models_and_migration_do_not_drift`) reads the model, which maps
        # `Mapped[float]` to DOUBLE. A Float here is a column the model would ask to alter.
        batch_op.add_column(sa.Column("tick_volume", sa.Double(), nullable=True))


def downgrade() -> None:
    """Back to 0007 exactly: the column goes, and with it the volumes it held.

    A downgrade loses the measurement, which is what a downgrade means. It cannot be derived
    from anything else in the database, so there is nothing for `upgrade` to rebuild: the
    volume comes back only when the broker is read again.
    """
    with op.batch_alter_table("candles", schema=None) as batch_op:
        batch_op.drop_column("tick_volume")
