"""scope historical forward source dedup to batch

Revision ID: 20251214_scope_historical_forward_source_dedup_to_batch
Revises: 20251213_add_historical_forward_receipts
"""

from alembic import op


revision = "20251214_scope_historical_forward_source_dedup_to_batch"
down_revision = "20251213_add_historical_forward_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_hist_forward_source_revision",
        "historical_forward_receipts",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_hist_forward_source_revision",
        "historical_forward_receipts",
        ["batch_id", "source_chat_id", "source_message_id", "source_message_revision"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_hist_forward_source_revision",
        "historical_forward_receipts",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_hist_forward_source_revision",
        "historical_forward_receipts",
        ["source_chat_id", "source_message_id", "source_message_revision"],
    )
