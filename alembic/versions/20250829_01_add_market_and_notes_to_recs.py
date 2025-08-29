# --- START OF FILE: alembic/versions/20250829_01_add_market_and_notes_to_recs.py ---
"""add market and notes columns to recommendations

Revision ID: 20250829_01
Revises: <PUT_PREVIOUS_REVISION_ID_HERE>
Create Date: 2025-08-29 15:40:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20250829_01"
down_revision = "<PUT_PREVIOUS_REVISION_ID_HERE>"
branch_labels = None
depends_on = None

def upgrade() -> None:
    with op.batch_alter_table("recommendations") as batch:
        batch.add_column(sa.Column("market", sa.String(), nullable=True))
        batch.add_column(sa.Column("notes", sa.Text(), nullable=True))

def downgrade() -> None:
    with op.batch_alter_table("recommendations") as batch:
        batch.drop_column("notes")
        batch.drop_column("market")
# --- END OF FILE ---