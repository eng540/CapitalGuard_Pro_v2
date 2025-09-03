# --- START OF FILE: alembic/versions/20250903_add_order_type.py ---
"""add order type to recommendations

Revision ID: 20250903_add_order_type
Revises: 20250903_add_lifecycle
Create Date: 2025-09-03 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20250903_add_order_type'
down_revision = '20250903_add_lifecycle'
branch_labels = None
depends_on = None

# Define the new Enum type for PostgreSQL
order_type_enum = sa.Enum('MARKET', 'LIMIT', 'STOP_MARKET', name='ordertype')

def upgrade() -> None:
    """
    Applies the changes to the database.
    This function creates the 'ordertype' ENUM and adds the 'order_type' column
    to the recommendations table with a sensible default for existing rows.
    """
    # Create the new ENUM type in the database before using it in a column.
    # checkfirst=True prevents an error if the type already exists (e.g., in testing).
    order_type_enum.create(op.get_bind(), checkfirst=True)
    
    # Add the new column to the table.
    op.add_column('recommendations', sa.Column(
        'order_type',
        order_type_enum,
        nullable=False,
        # Set a server-side default. For all existing recommendations, we assume
        # they were Limit orders, which is the most common type.
        server_default='LIMIT'
    ))

def downgrade() -> None:
    """
    Reverts the changes from the database.
    This function drops the 'order_type' column and then removes the 'ordertype' ENUM.
    """
    op.drop_column('recommendations', 'order_type')
    
    # Drop the ENUM type from the database after it's no longer in use.
    order_type_enum.drop(op.get_bind(), checkfirst=True)
# --- END OF FILE ---