"""add historical content interpretations

Revision ID: 20260823_hist_content_interpretations
Revises: 20260823_hist_message_foundation
"""
from alembic import op
import sqlalchemy as sa

revision = "20260823_hist_content_interpretations"
down_revision = "20260823_hist_message_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "historical_content_interpretations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision_id", sa.Integer(), sa.ForeignKey("historical_message_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content_type", sa.String(length=40), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("classifier_version", sa.String(length=40), nullable=False),
        sa.Column("classifier_method", sa.String(length=40), nullable=False, server_default="G2_DETERMINISTIC_RULES"),
        sa.Column("meaning_json", sa.JSON()),
        sa.Column("provenance_json", sa.JSON(), nullable=False),
        sa.Column("ambiguity_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("revision_id", "classifier_version", name="uq_hist_content_interpretation_revision_version"),
    )
    op.create_index("ix_hist_content_interpretation_revision", "historical_content_interpretations", ["revision_id"])
    op.create_index("ix_hist_content_interpretation_type", "historical_content_interpretations", ["content_type"])


def downgrade():
    op.drop_table("historical_content_interpretations")
