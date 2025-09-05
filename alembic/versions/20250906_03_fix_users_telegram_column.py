"""Fixes the state of the telegram_user_id column in the users table.

Revision ID: 20250906_03_fix_users_column
Revises: 20250906_02_rename_telegram_id
Create Date: 2025-09-06 02:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20250906_03_fix_users_column'
down_revision = '20250906_02_rename_telegram_id'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    This is a "surgical patch" migration. It inspects the 'users' table
    and ensures the 'telegram_user_id' column exists with the correct name,
    regardless of the previous failed states.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    
    # Check if the 'users' table exists first to prevent errors on fresh setups
    if not inspector.has_table("users"):
        print("Table 'users' not found. Skipping column fix, will be created by initial migration.")
        return

    columns = [c.get('name') for c in inspector.get_columns('users')]

    correct_column = 'telegram_user_id'
    wrong_column = 'telegram_id'

    if correct_column in columns:
        # The column is already correct, do nothing.
        print(f"Column '{correct_column}' already exists. No action needed.")
    elif wrong_column in columns:
        # The column has the wrong name, rename it.
        print(f"Found column '{wrong_column}'. Renaming to '{correct_column}'.")
        op.alter_column('users', wrong_column, new_column_name=correct_column)
    else:
        # Neither column exists, so the table is incomplete. Add the correct column.
        print(f"Column '{correct_column}' not found. Adding it to the 'users' table.")
        op.add_column('users', sa.Column(correct_column, sa.BigInteger(), nullable=True))
        # We make it nullable=True initially to handle existing rows, then set it to False
        op.execute(f"UPDATE users SET {correct_column} = 0 WHERE {correct_column} IS NULL")
        op.alter_column('users', correct_column, nullable=False)


def downgrade() -> None:
    # This downgrade is for completeness but is unlikely to be used.
    # It assumes the final state is 'telegram_user_id' and renames it back.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c.get('name') for c in inspector.get_columns('users')]

    if 'telegram_user_id' in columns:
        op.alter_column('users', 'telegram_user_id', new_column_name='telegram_id')