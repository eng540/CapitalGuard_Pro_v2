"""Add profit stop fields to recommendations and ensure user_trades forwarded text column exists.

Revision ID: 20251022_add_profit_stop_fields
Revises: 20251007_v3_baseline
Create Date: 2025-10-22 20:15:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20251022_add_profit_stop_fields'
down_revision = '20251007_v3_baseline'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add columns to recommendations
    op.add_column('recommendations', sa.Column('profit_stop_mode', sa.String(length=32), nullable=False, server_default='NONE'))
    op.add_column('recommendations', sa.Column('profit_stop_price', sa.Numeric(precision=20, scale=8), nullable=True))
    op.add_column('recommendations', sa.Column('profit_stop_trailing_value', sa.Numeric(precision=20, scale=8), nullable=True))
    op.add_column('recommendations', sa.Column('profit_stop_active', sa.Boolean(), nullable=False, server_default=sa.text('false')))

    # Ensure user_trades.source_forwarded_text exists (idempotent)
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('user_trades')]
    if 'source_forwarded_text' not in columns:
        op.add_column('user_trades', sa.Column('source_forwarded_text', sa.Text(), nullable=True))

    # Create indexes if helpful
    op.create_index(op.f('ix_recommendations_profit_stop_active'), 'recommendations', ['profit_stop_active'], unique=False)


def downgrade() -> None:
    try:
        op.drop_index(op.f('ix_recommendations_profit_stop_active'), table_name='recommendations')
    except Exception:
        pass
    for col in ['profit_stop_active', 'profit_stop_trailing_value', 'profit_stop_price', 'profit_stop_mode']:
        try:
            op.drop_column('recommendations', col)
        except Exception:
            pass
    # Do not drop source_forwarded_text in downgrade to avoid data loss