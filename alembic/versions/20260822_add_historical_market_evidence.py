"""add historical market evidence artifacts

Revision ID: 20260822_add_historical_market_evidence
Revises: 20260822_add_usertrade_cancelled_status
"""
from alembic import op
import sqlalchemy as sa


revision = "20260822_add_historical_market_evidence"
down_revision = "20260822_add_usertrade_cancelled_status"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "historical_market_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("signal_id", sa.Integer(), sa.ForeignKey("historical_signals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("replay_run_ref", sa.String(length=48), nullable=False, unique=True),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("provider_endpoint", sa.String(length=255), nullable=True),
        sa.Column("asset", sa.String(length=80), nullable=False),
        sa.Column("market", sa.String(length=80), nullable=True),
        sa.Column("interval", sa.String(length=16), server_default="1m", nullable=False),
        sa.Column("range_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("range_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("candle_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("artifact_hash", sa.String(length=64), nullable=False),
        sa.Column("artifact_key", sa.String(length=180), nullable=False, unique=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_historical_market_evidence_signal_id", "historical_market_evidence", ["signal_id"])
    op.create_index("ix_historical_market_evidence_replay_run_ref", "historical_market_evidence", ["replay_run_ref"])
    op.create_index("ix_historical_market_evidence_provider", "historical_market_evidence", ["provider"])
    op.create_index("ix_historical_market_evidence_asset", "historical_market_evidence", ["asset"])
    op.create_index("ix_historical_market_evidence_market", "historical_market_evidence", ["market"])
    op.create_index("ix_historical_market_evidence_range_start", "historical_market_evidence", ["range_start"])
    op.create_index("ix_historical_market_evidence_range_end", "historical_market_evidence", ["range_end"])
    op.create_index("ix_historical_market_evidence_artifact_hash", "historical_market_evidence", ["artifact_hash"])


def downgrade():
    for name in [
        "ix_historical_market_evidence_artifact_hash",
        "ix_historical_market_evidence_range_end",
        "ix_historical_market_evidence_range_start",
        "ix_historical_market_evidence_market",
        "ix_historical_market_evidence_asset",
        "ix_historical_market_evidence_provider",
        "ix_historical_market_evidence_replay_run_ref",
        "ix_historical_market_evidence_signal_id",
    ]:
        op.drop_index(name, table_name="historical_market_evidence")
    op.drop_table("historical_market_evidence")
