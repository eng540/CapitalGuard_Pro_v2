"""add G5 historical signal materialization bridge

Revision ID: 20260823_hist_signal_materializations
Revises: 20260823_hist_adjudication_drafts
"""
from alembic import op
import sqlalchemy as sa


revision = "20260823_hist_signal_materializations"
down_revision = "20260823_hist_adjudication_drafts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "historical_signal_materializations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("draft_id", sa.Integer(), sa.ForeignKey("historical_recommendation_drafts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("signal_id", sa.Integer(), sa.ForeignKey("historical_signals.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_id", sa.Integer(), sa.ForeignKey("historical_message_revisions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("evidence_id", sa.Integer(), sa.ForeignKey("historical_signal_evidence.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("materialization_kind", sa.String(length=40), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("draft_id", name="uq_hist_signal_materialization_draft"),
        sa.UniqueConstraint("signal_id", name="uq_hist_signal_materialization_signal"),
    )
    for name, column in (("ix_hist_signal_materialization_draft", "draft_id"), ("ix_hist_signal_materialization_signal", "signal_id"), ("ix_hist_signal_materialization_revision", "revision_id"), ("ix_hist_signal_materialization_evidence", "evidence_id"), ("ix_hist_signal_materialization_kind", "materialization_kind"), ("ix_hist_signal_materialization_source_timestamp", "source_timestamp")):
        op.create_index(name, "historical_signal_materializations", [column])


def downgrade():
    op.drop_table("historical_signal_materializations")
