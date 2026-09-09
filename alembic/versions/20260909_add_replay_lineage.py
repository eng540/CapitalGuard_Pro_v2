"""Add explicit non-destructive HistoricalReplayRun lineage.

Revision ID: 20260909_add_replay_lineage
Revises: 20260908_repair_evidence_seq
"""

from alembic import op
import sqlalchemy as sa


revision = "20260909_add_replay_lineage"
down_revision = "20260908_repair_evidence_seq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "historical_replay_runs",
        sa.Column("reprocess_of_run_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_historical_replay_runs_reprocess_of_run_id",
        "historical_replay_runs",
        ["reprocess_of_run_id"],
    )
    op.create_foreign_key(
        "fk_historical_replay_runs_reprocess_of_run_id",
        "historical_replay_runs",
        "historical_replay_runs",
        ["reprocess_of_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_historical_replay_runs_reprocess_of_run_id",
        "historical_replay_runs",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_historical_replay_runs_reprocess_of_run_id",
        table_name="historical_replay_runs",
    )
    op.drop_column("historical_replay_runs", "reprocess_of_run_id")
