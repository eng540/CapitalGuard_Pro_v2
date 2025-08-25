"""add exit_price and closed_at

Revision ID: 20250825_add_exit_price_closed_at
Revises:
Create Date: 2025-08-25 12:00:00
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20250825_add_exit_price_closed_at"
down_revision = None  # 👈 إذا كان لديك هجرات سابقة، غيّرها لآخر Revision ID لديك
branch_labels = None
depends_on = None

def upgrade() -> None:
    with op.batch_alter_table("recommendations") as batch_op:
        batch_op.add_column(sa.Column("exit_price", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("closed_at", sa.DateTime(), nullable=True))

def downgrade() -> None:
    with op.batch_alter_table("recommendations") as batch_op:
        batch_op.drop_column("closed_at")
        batch_op.drop_column("exit_price")