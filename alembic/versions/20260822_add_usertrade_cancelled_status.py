"""Add the terminal UserTrade CANCELLED status.

Revision ID: 20260822_add_usertrade_cancelled_status
Revises: 20260820_add_web_command_audit
"""

from alembic import op
import sqlalchemy as sa


revision = "20260822_add_usertrade_cancelled_status"
down_revision = "20260820_add_web_command_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE usertradestatus ADD VALUE IF NOT EXISTS 'CANCELLED'")
    op.execute("ALTER TABLE user_trades DROP CONSTRAINT IF EXISTS valid_user_trade_status")
    op.execute(
        """
        ALTER TABLE user_trades
        ADD CONSTRAINT valid_user_trade_status
        CHECK (status IN ('WATCHLIST', 'PENDING_ACTIVATION', 'ACTIVATED', 'CLOSED', 'CANCELLED'))
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    cancelled_count = bind.execute(sa.text("SELECT COUNT(*) FROM user_trades WHERE status::text = 'CANCELLED'")).scalar_one()
    if cancelled_count:
        raise RuntimeError("Cannot downgrade while CANCELLED UserTrade rows exist")
    op.execute("ALTER TABLE user_trades DROP CONSTRAINT IF EXISTS valid_user_trade_status")
    op.execute(
        """
        ALTER TABLE user_trades
        ADD CONSTRAINT valid_user_trade_status
        CHECK (status IN ('WATCHLIST', 'PENDING_ACTIVATION', 'ACTIVATED', 'CLOSED'))
        """
    )
    # PostgreSQL enum values are intentionally retained; removing them would require a risky type rewrite.
