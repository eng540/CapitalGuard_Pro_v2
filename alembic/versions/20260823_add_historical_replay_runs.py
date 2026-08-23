"""add traceable historical replay runs for G6

Revision ID: 20260823_historical_replay_runs
Revises: 20260823_link_lifecycle_materializations
"""
from alembic import op
import sqlalchemy as sa

revision = "20260823_historical_replay_runs"
down_revision = "20260823_link_lifecycle_materializations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "historical_replay_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_ref", sa.String(length=48), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column("materialization_id", sa.Integer(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("replay_version", sa.String(length=32), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="CREATED"),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval", sa.String(length=16), nullable=False, server_default="1m"),
        sa.Column("limit_count", sa.Integer(), nullable=False, server_default="1500"),
        sa.Column("provider", sa.String(length=80), nullable=True),
        sa.Column("provider_endpoint", sa.String(length=255), nullable=True),
        sa.Column("data_source", sa.String(length=80), nullable=True),
        sa.Column("provider_metadata", sa.JSON(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_as_of_status", sa.String(length=40), nullable=False, server_default="UNAVAILABLE"),
        sa.Column("ambiguity_status", sa.String(length=24), nullable=False, server_default="NONE"),
        sa.Column("quality_status", sa.String(length=24), nullable=False, server_default="UNASSESSED"),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["signal_id"], ["historical_signals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["materialization_id"], ["historical_signal_materializations.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("run_ref", name="uq_historical_replay_run_ref"),
        sa.UniqueConstraint("request_fingerprint", name="uq_historical_replay_run_request_fingerprint"),
    )
    op.create_index("ix_historical_replay_runs_signal_id", "historical_replay_runs", ["signal_id"])
    op.create_index("ix_historical_replay_runs_materialization_id", "historical_replay_runs", ["materialization_id"])
    op.create_index("ix_historical_replay_runs_status", "historical_replay_runs", ["status"])
    op.create_index("ix_historical_replay_runs_provider", "historical_replay_runs", ["provider"])
    with op.batch_alter_table("historical_market_evidence", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("replay_run_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_historical_market_evidence_replay_run", "historical_replay_runs", ["replay_run_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_historical_market_evidence_replay_run_id", "historical_market_evidence", ["replay_run_id"])
    with op.batch_alter_table("historical_signal_events", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("replay_run_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_historical_signal_events_replay_run", "historical_replay_runs", ["replay_run_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_historical_signal_events_replay_run_id", "historical_signal_events", ["replay_run_id"])


def downgrade():
    op.drop_index("ix_historical_signal_events_replay_run_id", table_name="historical_signal_events")
    with op.batch_alter_table("historical_signal_events", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_historical_signal_events_replay_run", type_="foreignkey")
        batch_op.drop_column("replay_run_id")
    op.drop_index("ix_historical_market_evidence_replay_run_id", table_name="historical_market_evidence")
    with op.batch_alter_table("historical_market_evidence", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_historical_market_evidence_replay_run", type_="foreignkey")
        batch_op.drop_column("replay_run_id")
    op.drop_index("ix_historical_replay_runs_provider", table_name="historical_replay_runs")
    op.drop_index("ix_historical_replay_runs_status", table_name="historical_replay_runs")
    op.drop_index("ix_historical_replay_runs_materialization_id", table_name="historical_replay_runs")
    op.drop_index("ix_historical_replay_runs_signal_id", table_name="historical_replay_runs")
    op.drop_table("historical_replay_runs")
