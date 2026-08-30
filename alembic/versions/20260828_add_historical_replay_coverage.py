"""Persist G6 historical coverage evidence on replay runs."""

from alembic import op
import sqlalchemy as sa


revision = "20260828_add_historical_replay_coverage"
down_revision = "20260825_repair_historical_market_evidence_sequence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("historical_replay_runs", sa.Column("coverage_status", sa.String(length=24), nullable=True))
    op.add_column("historical_replay_runs", sa.Column("coverage_ratio", sa.Float(), nullable=True))
    op.add_column("historical_replay_runs", sa.Column("actual_start", sa.DateTime(timezone=True), nullable=True))
    op.add_column("historical_replay_runs", sa.Column("actual_end", sa.DateTime(timezone=True), nullable=True))
    op.add_column("historical_replay_runs", sa.Column("dataset_hash", sa.String(length=64), nullable=True))

    op.create_index("ix_historical_replay_runs_coverage_status", "historical_replay_runs", ["coverage_status"])
    op.create_index("ix_historical_replay_runs_dataset_hash", "historical_replay_runs", ["dataset_hash"])


def downgrade() -> None:
    op.drop_index("ix_historical_replay_runs_dataset_hash", table_name="historical_replay_runs")
    op.drop_index("ix_historical_replay_runs_coverage_status", table_name="historical_replay_runs")
    op.drop_column("historical_replay_runs", "dataset_hash")
    op.drop_column("historical_replay_runs", "actual_end")
    op.drop_column("historical_replay_runs", "actual_start")
    op.drop_column("historical_replay_runs", "coverage_ratio")
    op.drop_column("historical_replay_runs", "coverage_status")
