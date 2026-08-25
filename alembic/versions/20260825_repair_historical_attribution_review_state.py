"""Repair historical attribution review state columns on legacy databases.

Revision ID: 20260825_repair_historical_attribution_review_state
Revises: 20260824_add_usertrade_profit_stop_fields
"""
from alembic import op
import sqlalchemy as sa


revision = "20260825_repair_historical_attribution_review_state"
down_revision = "20260824_add_usertrade_profit_stop_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("historical_signal_attributions")}

    if "reviewed_at" not in columns:
        op.add_column(
            "historical_signal_attributions",
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "review_note" not in columns:
        op.add_column(
            "historical_signal_attributions",
            sa.Column("review_note", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("historical_signal_attributions")}

    if "review_note" in columns:
        op.drop_column("historical_signal_attributions", "review_note")
    if "reviewed_at" in columns:
        op.drop_column("historical_signal_attributions", "reviewed_at")
