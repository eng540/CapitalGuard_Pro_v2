"""add unclaimed historical shadow channels

Revision ID: 20251215_add_historical_shadow_channels
Revises: 20251214_scope_historical_forward_source_dedup_to_batch
"""

from alembic import op
import sqlalchemy as sa


revision = "20251215_add_historical_shadow_channels"
down_revision = "20251214_scope_historical_forward_source_dedup_to_batch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "historical_shadow_channels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("claim_status", sa.String(length=24), nullable=False, server_default="UNCLAIMED"),
        sa.Column(
            "discovered_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "canonical_channel_catalog_id",
            sa.Integer(),
            sa.ForeignKey("channel_catalog.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.UniqueConstraint("telegram_channel_id", name="uq_historical_shadow_telegram_channel"),
    )
    op.create_index(
        "ix_historical_shadow_channels_telegram_channel_id",
        "historical_shadow_channels",
        ["telegram_channel_id"],
    )
    op.create_index(
        "ix_historical_shadow_channels_claim_status",
        "historical_shadow_channels",
        ["claim_status"],
    )
    op.create_index(
        "ix_historical_shadow_channels_discovered_by_user_id",
        "historical_shadow_channels",
        ["discovered_by_user_id"],
    )
    op.create_index(
        "ix_historical_shadow_channels_canonical_channel_catalog_id",
        "historical_shadow_channels",
        ["canonical_channel_catalog_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_historical_shadow_channels_canonical_channel_catalog_id",
        table_name="historical_shadow_channels",
    )
    op.drop_index(
        "ix_historical_shadow_channels_discovered_by_user_id",
        table_name="historical_shadow_channels",
    )
    op.drop_index(
        "ix_historical_shadow_channels_claim_status",
        table_name="historical_shadow_channels",
    )
    op.drop_index(
        "ix_historical_shadow_channels_telegram_channel_id",
        table_name="historical_shadow_channels",
    )
    op.drop_table("historical_shadow_channels")
