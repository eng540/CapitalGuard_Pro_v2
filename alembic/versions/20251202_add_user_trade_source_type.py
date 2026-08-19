"""add source type to user trades

Revision ID: 20251202_add_user_trade_source_type
Revises: 20251201_add_dedup_ledger
"""

from alembic import op
import sqlalchemy as sa


revision = "20251202_add_user_trade_source_type"
down_revision = "20251201_add_dedup_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_trades",
        sa.Column("source_type", sa.String(length=32), nullable=False, server_default="FORWARD"),
    )
    op.create_index("ix_user_trades_source_type", "user_trades", ["source_type"])


def downgrade() -> None:
    op.drop_index("ix_user_trades_source_type", table_name="user_trades")
    op.drop_column("user_trades", "source_type")
