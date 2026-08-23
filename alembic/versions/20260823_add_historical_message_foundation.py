"""add historical message foundation

Revision ID: 20260823_hist_message_foundation
Revises: 20260823_repair_historical_attribution_review_columns
"""
from alembic import op
import sqlalchemy as sa

revision = "20260823_hist_message_foundation"
down_revision = "20260823_repair_historical_attribution_review_columns"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("historical_canonical_messages", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("source_kind", sa.String(length=32), nullable=False), sa.Column("source_chat_id", sa.BigInteger(), nullable=False), sa.Column("external_message_id", sa.BigInteger(), nullable=False), sa.Column("ingestion_mode", sa.String(length=24), nullable=False, server_default="HISTORICAL"), sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False), sa.Column("latest_revision_number", sa.Integer(), nullable=False, server_default="0"), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False), sa.UniqueConstraint("source_kind", "source_chat_id", "external_message_id", name="uq_hist_canonical_source_message"))
    op.create_index("ix_hist_canonical_source_chat", "historical_canonical_messages", ["source_chat_id"])
    op.create_table("historical_message_revisions", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("message_id", sa.Integer(), sa.ForeignKey("historical_canonical_messages.id", ondelete="CASCADE"), nullable=False), sa.Column("revision_number", sa.Integer(), nullable=False), sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False), sa.Column("source_timestamp", sa.DateTime(timezone=True)), sa.Column("source_edit_date", sa.DateTime(timezone=True)), sa.Column("content_hash", sa.String(length=64), nullable=False), sa.Column("raw_text", sa.Text()), sa.Column("source_origin_type", sa.String(length=40), nullable=False), sa.Column("source_reply_to_message_id", sa.BigInteger()), sa.Column("safe_classification", sa.String(length=32), nullable=False, server_default="UNKNOWN"), sa.Column("classification_confidence", sa.Numeric(5, 4), nullable=False, server_default="0"), sa.Column("classification_method", sa.String(length=40), nullable=False, server_default="G1_SAFE_RULES"), sa.Column("receipt_id", sa.Integer(), sa.ForeignKey("historical_forward_receipts.id", ondelete="SET NULL")), sa.Column("evidence_id", sa.Integer(), sa.ForeignKey("historical_signal_evidence.id", ondelete="SET NULL")), sa.Column("provenance_json", sa.JSON()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False), sa.UniqueConstraint("message_id", "revision_number", name="uq_hist_message_revision_number"), sa.UniqueConstraint("message_id", "content_hash", name="uq_hist_message_revision_hash"))
    op.create_table("historical_message_relationships", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("source_message_id", sa.Integer(), sa.ForeignKey("historical_canonical_messages.id", ondelete="CASCADE"), nullable=False), sa.Column("target_message_id", sa.Integer(), sa.ForeignKey("historical_canonical_messages.id", ondelete="CASCADE"), nullable=False), sa.Column("relationship_type", sa.String(length=40), nullable=False), sa.Column("confidence_score", sa.Numeric(5, 4), nullable=False, server_default="0"), sa.Column("method", sa.String(length=40), nullable=False, server_default="G1_SAFE_RULES"), sa.Column("evidence_json", sa.JSON()), sa.Column("review_status", sa.String(length=24), nullable=False, server_default="PENDING"), sa.Column("reviewed_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")), sa.Column("reviewed_at", sa.DateTime(timezone=True)), sa.Column("review_note", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False), sa.UniqueConstraint("source_message_id", "target_message_id", "relationship_type", "method", name="uq_hist_message_relationship"))


def downgrade():
    op.drop_table("historical_message_relationships")
    op.drop_table("historical_message_revisions")
    op.drop_table("historical_canonical_messages")
