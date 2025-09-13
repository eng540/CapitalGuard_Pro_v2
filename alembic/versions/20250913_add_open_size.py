# --- START OF NEW MIGRATION FILE ---
"""Add open_size_percent to recommendations table

Revision ID: 20250913_add_open_size
Revises: <ID_الترحيل_السابق_لديك>
Create Date: 2025-09-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20250913_add_open_size'
# ⚠️ هام: تأكد من أن هذا يطابق آخر ملف ترحيل لديك
down_revision = '20250912_add_exit_strategy_and_profit_stop'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('recommendations', sa.Column(
        'open_size_percent',
        sa.Float(),
        nullable=False,
        server_default=sa.text('100.0'),
        default=100.0
    ))


def downgrade() -> None:
    op.drop_column('recommendations', 'open_size_percent')
# --- END OF NEW MIGRATION FILE ---