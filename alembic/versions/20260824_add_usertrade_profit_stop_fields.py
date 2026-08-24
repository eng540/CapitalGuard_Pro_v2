"""Add canonical profit-stop policy fields to user trades.

Revision ID: 20260824_add_usertrade_profit_stop_fields
Revises: 20260823_historical_replay_runs
Create Date: 2026-08-24
"""
from alembic import op
import sqlalchemy as sa

revision = "20260824_add_usertrade_profit_stop_fields"
down_revision = "20260823_historical_replay_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_trades", sa.Column("profit_stop_mode", sa.String(length=32), server_default="NONE", nullable=False))
    op.add_column("user_trades", sa.Column("profit_stop_price", sa.Numeric(precision=20, scale=8), nullable=True))
    op.add_column("user_trades", sa.Column("profit_stop_trailing_value", sa.Numeric(precision=20, scale=8), nullable=True))
    op.add_column("user_trades", sa.Column("profit_stop_active", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("user_trades", sa.Column("break_even_after_profit_pct", sa.Numeric(precision=10, scale=4), nullable=True))
    op.add_column("user_trades", sa.Column("break_even_buffer", sa.Numeric(precision=20, scale=8), server_default="0", nullable=False))
    op.add_column("recommendations", sa.Column("break_even_after_profit_pct", sa.Numeric(precision=10, scale=4), nullable=True))
    op.add_column("recommendations", sa.Column("break_even_buffer", sa.Numeric(precision=20, scale=8), server_default="0", nullable=False))
    op.create_index(op.f("ix_user_trades_profit_stop_active"), "user_trades", ["profit_stop_active"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_user_trades_profit_stop_active"), table_name="user_trades")
    op.drop_column("recommendations", "break_even_buffer")
    op.drop_column("recommendations", "break_even_after_profit_pct")
    op.drop_column("user_trades", "break_even_buffer")
    op.drop_column("user_trades", "break_even_after_profit_pct")
    op.drop_column("user_trades", "profit_stop_active")
    op.drop_column("user_trades", "profit_stop_trailing_value")
    op.drop_column("user_trades", "profit_stop_price")
    op.drop_column("user_trades", "profit_stop_mode")
