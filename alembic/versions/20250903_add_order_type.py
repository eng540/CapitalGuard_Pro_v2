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
    order_type_enum.create(op.get_bind(), checkfirst=True)
    op.add_column('recommendations', sa.Column(
        'order_type',
        order_type_enum,
        nullable=False,
        server_default='LIMIT' # Set a sensible default for existing rows
    ))

def downgrade() -> None:
    op.drop_column('recommendations', 'order_type')
    order_type_enum.drop(op.get_bind(), checkfirst=True)
# --- END OF FILE ---