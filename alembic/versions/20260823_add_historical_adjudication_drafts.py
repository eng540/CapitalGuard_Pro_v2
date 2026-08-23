"""add historical adjudication drafts

Revision ID: 20260823_hist_adjudication_drafts
Revises: 20260823_hist_financial_candidates
"""
from alembic import op
import sqlalchemy as sa
revision = "20260823_hist_adjudication_drafts"
down_revision = "20260823_hist_financial_candidates"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("historical_recommendation_drafts", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("revision_id", sa.Integer(), sa.ForeignKey("historical_message_revisions.id", ondelete="CASCADE"), nullable=False), sa.Column("related_draft_id", sa.Integer(), sa.ForeignKey("historical_recommendation_drafts.id", ondelete="SET NULL")), sa.Column("draft_kind", sa.String(length=40), nullable=False), sa.Column("confidence_score", sa.Numeric(5,4), nullable=False), sa.Column("status", sa.String(length=24), nullable=False, server_default="DRAFT"), sa.Column("evidence_chain_json", sa.JSON(), nullable=False), sa.Column("adjudication_reason", sa.Text()), sa.Column("reviewed_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")), sa.Column("reviewed_at", sa.DateTime(timezone=True)), sa.Column("review_note", sa.Text()), sa.Column("override_json", sa.JSON()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False), sa.UniqueConstraint("revision_id", "draft_kind", name="uq_hist_draft_revision_kind"))
def downgrade(): op.drop_table("historical_recommendation_drafts")
