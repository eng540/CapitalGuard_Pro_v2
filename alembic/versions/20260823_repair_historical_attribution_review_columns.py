"""Repair historical attribution review columns on legacy databases.

Revision ID: 20260823_repair_historical_attribution_review_columns
Revises: 20260822_add_historical_market_evidence
"""
from alembic import op
import sqlalchemy as sa


revision = "20260823_repair_historical_attribution_review_columns"
down_revision = "20260822_add_historical_market_evidence"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("historical_signal_attributions")}
    if "reviewed_by_user_id" not in columns:
        op.add_column("historical_signal_attributions", sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True))
        op.create_foreign_key("fk_historical_signal_attributions_reviewed_by_user", "historical_signal_attributions", "users", ["reviewed_by_user_id"], ["id"], ondelete="SET NULL")
        op.create_index("ix_historical_signal_attributions_reviewed_by_user_id", "historical_signal_attributions", ["reviewed_by_user_id"])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("historical_signal_attributions")}
    if "reviewed_by_user_id" in columns:
        op.drop_index("ix_historical_signal_attributions_reviewed_by_user_id", table_name="historical_signal_attributions")
        op.drop_constraint("fk_historical_signal_attributions_reviewed_by_user", "historical_signal_attributions", type_="foreignkey")
        op.drop_column("historical_signal_attributions", "reviewed_by_user_id")
