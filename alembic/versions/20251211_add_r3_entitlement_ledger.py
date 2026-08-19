"""add non-commercial entitlement and ledger tables

Revision ID: 20251211_add_r3_entitlement_ledger
Revises: 20251210_add_analyst_profile_metadata
"""
from alembic import op
import sqlalchemy as sa

revision = "20251211_add_r3_entitlement_ledger"
down_revision = "20251210_add_analyst_profile_metadata"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "entitlement_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("feature_code", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=32), server_default="ALPHA_GRANT", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="GRANTED", nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False, unique=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_entitlement_grants_user_id", "entitlement_grants", ["user_id"])
    op.create_index("ix_entitlement_grants_feature_code", "entitlement_grants", ["feature_code"])
    op.create_index("ix_entitlement_grants_status", "entitlement_grants", ["status"])

    op.create_table(
        "subscription_ledger_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entry_type", sa.String(length=32), nullable=False),
        sa.Column("plan_code", sa.String(length=80), nullable=True),
        sa.Column("feature_code", sa.String(length=80), nullable=True),
        sa.Column("amount_minor", sa.Integer(), server_default="0", nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="USD", nullable=False),
        sa.Column("provider", sa.String(length=32), server_default="INTERNAL", nullable=False),
        sa.Column("provider_event_id", sa.String(length=160), nullable=True, unique=True),
        sa.Column("status", sa.String(length=20), server_default="RECORDED", nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False, unique=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_subscription_ledger_entries_user_id", "subscription_ledger_entries", ["user_id"])
    op.create_index("ix_subscription_ledger_entries_entry_type", "subscription_ledger_entries", ["entry_type"])
    op.create_index("ix_subscription_ledger_entries_plan_code", "subscription_ledger_entries", ["plan_code"])
    op.create_index("ix_subscription_ledger_entries_feature_code", "subscription_ledger_entries", ["feature_code"])
    op.create_index("ix_subscription_ledger_entries_status", "subscription_ledger_entries", ["status"])


def downgrade():
    op.drop_index("ix_subscription_ledger_entries_status", table_name="subscription_ledger_entries")
    op.drop_index("ix_subscription_ledger_entries_feature_code", table_name="subscription_ledger_entries")
    op.drop_index("ix_subscription_ledger_entries_plan_code", table_name="subscription_ledger_entries")
    op.drop_index("ix_subscription_ledger_entries_entry_type", table_name="subscription_ledger_entries")
    op.drop_index("ix_subscription_ledger_entries_user_id", table_name="subscription_ledger_entries")
    op.drop_table("subscription_ledger_entries")
    op.drop_index("ix_entitlement_grants_status", table_name="entitlement_grants")
    op.drop_index("ix_entitlement_grants_feature_code", table_name="entitlement_grants")
    op.drop_index("ix_entitlement_grants_user_id", table_name="entitlement_grants")
    op.drop_table("entitlement_grants")
