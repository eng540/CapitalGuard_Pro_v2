"""add historical forwarding receipts

Revision ID: 20251213_add_historical_forward_receipts
Revises: 20251212_add_historical_signal_reconstruction
"""

from alembic import op
import sqlalchemy as sa


revision = "20251213_add_historical_forward_receipts"
down_revision = "20251212_add_historical_signal_reconstruction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "historical_forward_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("historical_import_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_id", sa.Integer(), sa.ForeignKey("historical_signal_evidence.id", ondelete="SET NULL"), nullable=True),
        sa.Column("forwarding_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("receiver_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("receiver_message_id", sa.BigInteger(), nullable=False),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("source_message_id", sa.BigInteger(), nullable=True),
        sa.Column("source_message_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_origin_type", sa.String(length=40), nullable=False),
        sa.Column("source_message_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_edit_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_reply_to_message_id", sa.BigInteger(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("validation_status", sa.String(length=24), nullable=False, server_default="STAGED"),
        sa.Column("rejection_reason", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("receiver_chat_id", "receiver_message_id", name="uq_hist_forward_receiver_message"),
        sa.UniqueConstraint(
            "source_chat_id",
            "source_message_id",
            "source_message_revision",
            name="uq_hist_forward_source_revision",
        ),
    )
    op.create_index("ix_historical_forward_receipts_batch_id", "historical_forward_receipts", ["batch_id"])
    op.create_index("ix_historical_forward_receipts_evidence_id", "historical_forward_receipts", ["evidence_id"])
    op.create_index("ix_historical_forward_receipts_forwarding_user_id", "historical_forward_receipts", ["forwarding_user_id"])
    op.create_index("ix_historical_forward_receipts_receiver_chat_id", "historical_forward_receipts", ["receiver_chat_id"])
    op.create_index("ix_historical_forward_receipts_receiver_message_id", "historical_forward_receipts", ["receiver_message_id"])
    op.create_index("ix_historical_forward_receipts_source_chat_id", "historical_forward_receipts", ["source_chat_id"])
    op.create_index("ix_historical_forward_receipts_source_message_id", "historical_forward_receipts", ["source_message_id"])
    op.create_index("ix_historical_forward_receipts_source_message_timestamp", "historical_forward_receipts", ["source_message_timestamp"])
    op.create_index("ix_historical_forward_receipts_content_hash", "historical_forward_receipts", ["content_hash"])
    op.create_index("ix_historical_forward_receipts_validation_status", "historical_forward_receipts", ["validation_status"])


def downgrade() -> None:
    op.drop_index("ix_historical_forward_receipts_validation_status", table_name="historical_forward_receipts")
    op.drop_index("ix_historical_forward_receipts_content_hash", table_name="historical_forward_receipts")
    op.drop_index("ix_historical_forward_receipts_source_message_timestamp", table_name="historical_forward_receipts")
    op.drop_index("ix_historical_forward_receipts_source_message_id", table_name="historical_forward_receipts")
    op.drop_index("ix_historical_forward_receipts_source_chat_id", table_name="historical_forward_receipts")
    op.drop_index("ix_historical_forward_receipts_receiver_message_id", table_name="historical_forward_receipts")
    op.drop_index("ix_historical_forward_receipts_receiver_chat_id", table_name="historical_forward_receipts")
    op.drop_index("ix_historical_forward_receipts_forwarding_user_id", table_name="historical_forward_receipts")
    op.drop_index("ix_historical_forward_receipts_evidence_id", table_name="historical_forward_receipts")
    op.drop_index("ix_historical_forward_receipts_batch_id", table_name="historical_forward_receipts")
    op.drop_table("historical_forward_receipts")
