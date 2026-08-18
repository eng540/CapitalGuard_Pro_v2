"""Add durable deduplication ledger.

Revision ID: 20251201_add_dedup_ledger
Revises: 20251130_fix_stuck_shadow
"""

from alembic import op
import sqlalchemy as sa


revision = "20251201_add_dedup_ledger"
down_revision = "20251130_fix_stuck_shadow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dedup_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("source_channel_id", sa.BigInteger(), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False, server_default="accepted"),
        sa.Column("entity_type", sa.String(length=32), nullable=True),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "user_id",
            "source_channel_id",
            "fingerprint",
            "window_started_at",
            name="uq_dedup_user_channel_fingerprint_window",
        ),
    )
    op.create_index("ix_dedup_ledger_user_id", "dedup_ledger", ["user_id"])
    op.create_index("ix_dedup_ledger_source_channel_id", "dedup_ledger", ["source_channel_id"])
    op.create_index("ix_dedup_ledger_fingerprint", "dedup_ledger", ["fingerprint"])
    op.create_index("ix_dedup_ledger_window_started_at", "dedup_ledger", ["window_started_at"])


def downgrade() -> None:
    op.drop_index("ix_dedup_ledger_window_started_at", table_name="dedup_ledger")
    op.drop_index("ix_dedup_ledger_fingerprint", table_name="dedup_ledger")
    op.drop_index("ix_dedup_ledger_source_channel_id", table_name="dedup_ledger")
    op.drop_index("ix_dedup_ledger_user_id", table_name="dedup_ledger")
    op.drop_table("dedup_ledger")
