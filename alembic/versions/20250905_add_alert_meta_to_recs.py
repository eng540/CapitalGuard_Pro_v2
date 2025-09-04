# --- START OF FILE: alembic/versions/20250905_add_alert_meta_to_recs.py ---
"""Add alert_meta JSONB column to recommendations

Revision ID: 20250905_add_alert_meta
Revises: 20250904_repair_chain_and_set_timestamp_defaults
Create Date: 2025-09-05 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20250905_add_alert_meta'
# This should point to the last migration file you have.
down_revision = '20250904_repair_chain_and_set_timestamp_defaults'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Adds a non-nullable JSONB column to store alert state, with a default value.
    """
    # Add the column, allowing it to be nullable initially to handle existing rows.
    op.add_column('recommendations', sa.Column(
        'alert_meta',
        postgresql.JSONB(astext_for_array=False),
        nullable=True
    ))
    
    # Backfill all existing rows with a default empty JSON object.
    op.execute("UPDATE recommendations SET alert_meta = '{}'::jsonb WHERE alert_meta IS NULL;")
    
    # Now, alter the column to be non-nullable and set a server-side default for new rows.
    op.alter_column(
        'recommendations',
        'alert_meta',
        nullable=False,
        server_default=sa.text("'{}'::jsonb")
    )


def downgrade() -> None:
    """
    Removes the alert_meta column.
    """
    op.drop_column('recommendations', 'alert_meta')
# --- END OF FILE ---