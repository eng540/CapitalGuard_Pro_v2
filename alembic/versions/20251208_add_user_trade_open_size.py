"""add user trade open size for partial lifecycle sync

Revision ID: 20251208_add_user_trade_open_size
Revises: 20251207_add_recommendation_channel_refs
"""
from alembic import op
import sqlalchemy as sa

revision = "20251208_add_user_trade_open_size"
down_revision = "20251207_add_recommendation_channel_refs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_trades",
        sa.Column("open_size_percent", sa.Numeric(5, 2), nullable=False, server_default="100.00"),
    )
    op.execute("UPDATE user_trades SET open_size_percent = 0 WHERE status = 'CLOSED'")
    op.alter_column("user_trades", "open_size_percent", server_default=None)


def downgrade() -> None:
    op.drop_column("user_trades", "open_size_percent")
