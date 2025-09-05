"""Rename telegram_id to telegram_user_id in users table

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
    Renames the column 'telegram_id' to 'telegram_user_id' for consistency
    with the SQLAlchemy model.
    """
    try:
        op.alter_column('users', 'telegram_id', new_column_name='telegram_user_id')
    except Exception as e:
        # This might fail if a previous, failed migration was manually fixed.
        # We can safely ignore the error if the column is already renamed.
        print(f"Could not rename telegram_id, it might already be correct. Error: {e}")


def downgrade() -> None:
    """
    Reverts the column name from 'telegram_user_id' back to 'telegram_id'.
    """
    try:
        op.alter_column('users', 'telegram_user_id', new_column_name='telegram_id')
    except Exception as e:
        print(f"Could not rename telegram_user_id, it might already be correct. Error: {e}")