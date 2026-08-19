"""add durable recommendation publication delivery outbox

Revision ID: 20251203_add_publication_delivery
Revises: 20251202_add_user_trade_source_type
"""

from alembic import op
import sqlalchemy as sa


revision = "20251203_add_publication_delivery"
down_revision = "20251202_add_user_trade_source_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "publication_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recommendation_id", sa.Integer(), nullable=False),
        sa.Column("telegram_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["recommendation_id"], ["recommendations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_publication_delivery_idempotency_key"),
        sa.UniqueConstraint(
            "recommendation_id",
            "telegram_channel_id",
            "operation",
            name="uq_publication_delivery_target_operation",
        ),
    )
    op.create_index(
        "ix_publication_deliveries_recommendation_id",
        "publication_deliveries",
        ["recommendation_id"],
    )
    op.create_index(
        "ix_publication_deliveries_status",
        "publication_deliveries",
        ["status"],
    )
    op.create_index(
        "ix_publication_deliveries_next_attempt_at",
        "publication_deliveries",
        ["next_attempt_at"],
    )
    op.create_index(
        "ix_publication_deliveries_retry_queue",
        "publication_deliveries",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_publication_deliveries_retry_queue", table_name="publication_deliveries")
    op.drop_index("ix_publication_deliveries_next_attempt_at", table_name="publication_deliveries")
    op.drop_index("ix_publication_deliveries_status", table_name="publication_deliveries")
    op.drop_index("ix_publication_deliveries_recommendation_id", table_name="publication_deliveries")
    op.drop_table("publication_deliveries")
