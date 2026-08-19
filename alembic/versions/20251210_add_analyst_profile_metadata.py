"""add analyst profile metadata

Revision ID: 20251210_add_analyst_profile_metadata
Revises: 20251209_repair_tracked_trade_identity
"""
from alembic import op
import sqlalchemy as sa

revision = "20251210_add_analyst_profile_metadata"
down_revision = "20251209_repair_tracked_trade_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("analyst_profiles", sa.Column("specialty_market", sa.String(length=80), nullable=True))
    op.add_column("analyst_profiles", sa.Column("strategy_style", sa.String(length=80), nullable=True))
    op.add_column(
        "analyst_profiles",
        sa.Column(
            "profile_updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade():
    op.drop_column("analyst_profiles", "profile_updated_at")
    op.drop_column("analyst_profiles", "strategy_style")
    op.drop_column("analyst_profiles", "specialty_market")
