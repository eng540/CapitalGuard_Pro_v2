"""link lifecycle materializations to their parent signal materialization

Revision ID: 20260823_link_lifecycle_materializations
Revises: 20260823_hist_signal_materializations
"""
from alembic import op
import sqlalchemy as sa

revision = "20260823_link_lifecycle_materializations"
down_revision = "20260823_hist_signal_materializations"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("historical_signal_materializations", sa.Column("related_materialization_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_hist_signal_materialization_parent", "historical_signal_materializations", "historical_signal_materializations", ["related_materialization_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_hist_signal_materialization_parent", "historical_signal_materializations", ["related_materialization_id"])
    op.drop_constraint("uq_hist_signal_materialization_signal", "historical_signal_materializations", type_="unique")

def downgrade():
    op.create_unique_constraint("uq_hist_signal_materialization_signal", "historical_signal_materializations", ["signal_id"])
    op.drop_index("ix_hist_signal_materialization_parent", table_name="historical_signal_materializations")
    op.drop_constraint("fk_hist_signal_materialization_parent", "historical_signal_materializations", type_="foreignkey")
    op.drop_column("historical_signal_materializations", "related_materialization_id")
