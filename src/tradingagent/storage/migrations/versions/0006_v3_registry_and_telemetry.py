"""v3 registry and telemetry

Strategy lifecycle (cahier v3 §14, §30), research evidence (§9, §10), AI analyses and
proposals (§5, §15, §16, §39), execution telemetry (§20, §47) and the daily aggregates the
dashboard reads. The AI tables record what the AI observed; nothing here can move capital.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-07 06:15:00.000000
"""

import sqlalchemy as sa
from alembic import op

import tradingagent.storage.types

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TABLES = (
    "strategy_registry",
    "backtest_runs",
    "validation_runs",
    "ai_analyses",
    "ai_proposals",
    "execution_events",
    "daily_performance",
)
APPEND_ONLY = "execution_events"


def _enable_rls(table: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def _create_append_only(table: str) -> None:
    if op.get_bind().dialect.name == "sqlite":
        for event in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER {table}_no_{event.lower()} BEFORE {event} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
            )
        return
    op.execute(
        f"CREATE TRIGGER {table}_no_change BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION forbid_append_only_change()"
    )
    op.execute(
        f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
        "FOR EACH STATEMENT EXECUTE FUNCTION forbid_append_only_change()"
    )


def _drop_append_only(table: str) -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete")
        return
    op.execute(f"DROP TRIGGER IF EXISTS {table}_no_change ON {table}")
    op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}")


def upgrade() -> None:
    op.create_table(
        "strategy_registry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("ref", sa.String(length=80), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "discovered",
                "experimental",
                "backtesting",
                "validating",
                "paper",
                "candidate",
                "live",
                "deprecated",
                name="strategystatus",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("parent_ref", sa.String(length=80), nullable=True),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=True),
        sa.Column("dataset_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("promotion_reason", sa.Text(), nullable=True),
        sa.Column("created_at", tradingagent.storage.types.UtcDateTime(), nullable=False),
        sa.Column("promoted_at", tradingagent.storage.types.UtcDateTime(), nullable=True),
        sa.Column("updated_at", tradingagent.storage.types.UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_strategy_registry")),
        sa.UniqueConstraint("market", "ref", name=op.f("uq_strategy_registry_market_ref")),
    )
    with op.batch_alter_table("strategy_registry", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_strategy_registry_market_status"), ["market", "status"], unique=False
        )

    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ref", sa.String(length=80), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("dataset_id", sa.String(length=120), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("window_start", tradingagent.storage.types.UtcDateTime(), nullable=False),
        sa.Column("window_end", tradingagent.storage.types.UtcDateTime(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("costs", sa.JSON(), nullable=False),
        sa.Column("report_path", sa.String(length=255), nullable=True),
        sa.Column("created_at", tradingagent.storage.types.UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backtest_runs")),
    )
    with op.batch_alter_table("backtest_runs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_backtest_runs_ref_market"), ["ref", "market"], unique=False
        )

    op.create_table(
        "validation_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ref", sa.String(length=80), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column(
            "stage",
            sa.Enum(
                "backtest",
                "costs",
                "walk_forward",
                "out_of_sample",
                "monte_carlo",
                "stress",
                "parameter_robustness",
                "paper",
                "risk",
                name="validationstage",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", tradingagent.storage.types.UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_validation_runs")),
    )
    with op.batch_alter_table("validation_runs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_validation_runs_ref_stage"), ["ref", "stage"], unique=False
        )

    op.create_table(
        "ai_analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "loss_analysis",
                "degradation",
                "regime",
                "hypothesis",
                "postmortem",
                name="analysiskind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("ref", sa.String(length=80), nullable=True),
        sa.Column("signal_id", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("cost_eur", tradingagent.storage.types.ExactDecimal(length=48), nullable=True),
        sa.Column("created_at", tradingagent.storage.types.UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["signal_id"], ["signals.id"], name=op.f("fk_ai_analyses_signal_id_signals")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_analyses")),
    )
    with op.batch_alter_table("ai_analyses", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ai_analyses_kind_market"), ["kind", "market"], unique=False
        )

    op.create_table(
        "ai_proposals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("ref", sa.String(length=80), nullable=True),
        sa.Column("analysis_id", sa.Integer(), nullable=True),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("proposed_change", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "proposed",
                "validating",
                "rejected",
                "promoted",
                name="proposalstatus",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("created_at", tradingagent.storage.types.UtcDateTime(), nullable=False),
        sa.Column("decided_at", tradingagent.storage.types.UtcDateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["ai_analyses.id"],
            name=op.f("fk_ai_proposals_analysis_id_ai_analyses"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_proposals")),
    )
    with op.batch_alter_table("ai_proposals", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ai_proposals_status_market"), ["status", "market"], unique=False
        )

    op.create_table(
        "execution_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("signal_id", sa.Integer(), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "signal_generated",
                "order_requested",
                "order_sent",
                "order_accepted",
                "order_rejected",
                "filled",
                "position_opened",
                "position_closed",
                "stop_missing",
                "error",
                name="executioneventkind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("occurred_at", tradingagent.storage.types.UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_execution_events_order_id_orders")
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"], ["signals.id"], name=op.f("fk_execution_events_signal_id_signals")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_events")),
    )
    with op.batch_alter_table("execution_events", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_execution_events_symbol_time"), ["symbol", "occurred_at"], unique=False
        )

    op.create_table(
        "daily_performance",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("day", tradingagent.storage.types.UtcDateTime(), nullable=False),
        sa.Column(
            "mode",
            sa.Enum(
                "OBSERVATION",
                "SIGNAL",
                "PAPER",
                "DEMO",
                "LIVE",
                name="tradingmode",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("ref", sa.String(length=80), nullable=False),
        sa.Column("trades", sa.Integer(), nullable=False),
        sa.Column("wins", sa.Integer(), nullable=False),
        sa.Column("pnl", tradingagent.storage.types.ExactDecimal(length=48), nullable=False),
        sa.Column("risk_eur", tradingagent.storage.types.ExactDecimal(length=48), nullable=False),
        sa.Column("created_at", tradingagent.storage.types.UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_daily_performance")),
        sa.UniqueConstraint(
            "day", "mode", "market", "ref", name=op.f("uq_daily_performance_day_mode_market_ref")
        ),
    )

    # Telemetry is history: it is extended, never rewritten.
    _create_append_only(APPEND_ONLY)
    for table in TABLES:
        _enable_rls(table)


def downgrade() -> None:
    _drop_append_only(APPEND_ONLY)
    with op.batch_alter_table("execution_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_execution_events_symbol_time"))
    op.drop_table("execution_events")
    with op.batch_alter_table("ai_proposals", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ai_proposals_status_market"))
    op.drop_table("ai_proposals")
    with op.batch_alter_table("ai_analyses", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ai_analyses_kind_market"))
    op.drop_table("ai_analyses")
    with op.batch_alter_table("validation_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_validation_runs_ref_stage"))
    op.drop_table("validation_runs")
    with op.batch_alter_table("strategy_registry", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_strategy_registry_market_status"))
    op.drop_table("strategy_registry")
    op.drop_table("daily_performance")
    with op.batch_alter_table("backtest_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_backtest_runs_ref_market"))
    op.drop_table("backtest_runs")
