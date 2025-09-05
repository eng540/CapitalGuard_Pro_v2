"""Rename telegram_id to telegram_user_id in users table (Idempotent)

Revision ID: 20250906_02_rename_telegram_id
Revises: 20250905_01_add_user_foundation
Create Date: 2025-09-06 01:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20250906_02_rename_telegram_id'
down_revision = '20250905_01_add_user_foundation'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Safely renames the column 'telegram_id' to 'telegram_user_id' for consistency.
    This script now checks if the column exists before attempting to rename it.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c.get('name') for c in inspector.get_columns('users')]

    # Only attempt the rename if the old column name exists and the new one doesn't
    if 'telegram_id' in columns and 'telegram_user_id' not in columns:
        op.alter_column('users', 'telegram_id', new_column_name='telegram_user_id')
    else:
        # If the old column doesn't exist, we assume the migration is already
        # effectively complete or not needed. We can safely do nothing.
        print("Column 'telegram_id' not found in 'users' table, or 'telegram_user_id' already exists. Skipping rename.")


def downgrade() -> None:
    """
    Safely reverts the column name from 'telegram_user_id' back to 'telegram_id'.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c.get('name') for c in inspector.get_columns('users')]
    
    # Only attempt the rename if the new column name exists and the old one doesn't
    if 'telegram_user_id' in columns and 'telegram_id' not in columns:
        op.alter_column('users', 'telegram_user_id', new_column_name='telegram_id')
    else:
        print("Column 'telegram_user_id' not found in 'users' table, or 'telegram_id' already exists. Skipping rename.")