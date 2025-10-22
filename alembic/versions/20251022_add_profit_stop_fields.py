"""Add profit stop fields to recommendations

Revision ID: 20251022_add_profit_stop_fields
Revises: 20251007_v3_baseline
Create Date: 2025-10-22 21:45:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20251022_add_profit_stop_fields'
down_revision = '20251007_v3_baseline'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column('recommendations', sa.Column('profit_stop_mode', sa.String(length=32), server_default='NONE', nullable=False))
    op.add_column('recommendations', sa.Column('profit_stop_price', sa.Numeric(precision=20, scale=8), nullable=True))
    op.add_column('recommendations', sa.Column('profit_stop_trailing_value', sa.Numeric(precision=20, scale=8), nullable=True))
    op.add_column('recommendations', sa.Column('profit_stop_active', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.create_index(op.f('ix_recommendations_profit_stop_active'), 'recommendations', ['profit_stop_active'], unique=False)

def downgrade() -> None:
    op.drop_index(op.f('ix_recommendations_profit_stop_active'), table_name='recommendations')
    op.drop_column('recommendations', 'profit_stop_active')
    op.drop_column('recommendations', 'profit_stop_trailing_value')
    op.drop_column('recommendations', 'profit_stop_price')
    op.drop_column('recommendations', 'profit_stop_mode')