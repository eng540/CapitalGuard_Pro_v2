"""add historical financial candidates

Revision ID: 20260823_hist_financial_candidates
Revises: 20260823_hist_content_interpretations
"""
from alembic import op
import sqlalchemy as sa

revision = "20260823_hist_financial_candidates"
down_revision = "20260823_hist_content_interpretations"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("historical_financial_candidates", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("interpretation_id", sa.Integer(), sa.ForeignKey("historical_content_interpretations.id", ondelete="CASCADE"), nullable=False), sa.Column("field_type", sa.String(length=32), nullable=False), sa.Column("value_json", sa.JSON(), nullable=False), sa.Column("normalized_value", sa.String(length=160), nullable=False), sa.Column("span_text", sa.Text(), nullable=False), sa.Column("confidence_score", sa.Numeric(5,4), nullable=False), sa.Column("status", sa.String(length=24), nullable=False, server_default="CANDIDATE"), sa.Column("extractor_version", sa.String(length=40), nullable=False), sa.Column("provenance_json", sa.JSON(), nullable=False), sa.Column("review_status", sa.String(length=24), nullable=False, server_default="PENDING"), sa.Column("review_note", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False), sa.UniqueConstraint("interpretation_id", "field_type", "normalized_value", "span_text", "extractor_version", name="uq_hist_financial_candidate"))

def downgrade():
    op.drop_table("historical_financial_candidates")
