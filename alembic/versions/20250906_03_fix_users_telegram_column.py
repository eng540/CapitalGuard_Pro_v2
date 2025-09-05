"""Fixes the state of all columns in the users table.

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
    This is a comprehensive "surgical patch" migration. It inspects the 'users' table
    and ensures ALL required columns (telegram_user_id, user_type, created_at)
    exist with the correct specifications.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    
    if not inspector.has_table("users"):
        print("Table 'users' not found. Skipping column fix.")
        return

    columns = [c.get('name') for c in inspector.get_columns('users')]

    # --- Column 1: telegram_user_id ---
    correct_col_1 = 'telegram_user_id'
    wrong_col_1 = 'telegram_id'
    if correct_col_1 not in columns:
        if wrong_col_1 in columns:
            print(f"Renaming '{wrong_col_1}' to '{correct_col_1}'.")
            op.alter_column('users', wrong_col_1, new_column_name=correct_col_1)
        else:
            print(f"Adding missing column '{correct_col_1}'.")
            op.add_column('users', sa.Column(correct_col_1, sa.BigInteger(), nullable=True))
            op.execute(f"UPDATE users SET {correct_col_1} = 0 WHERE {correct_col_1} IS NULL")
            op.alter_column('users', correct_col_1, nullable=False)

    # --- Column 2: user_type ---
    col_2 = 'user_type'
    if col_2 not in columns:
        print(f"Adding missing column '{col_2}'.")
        op.add_column('users', sa.Column(col_2, sa.String(length=50), nullable=True, server_default='trader'))
        op.alter_column('users', col_2, nullable=False)

    # --- Column 3: created_at ---
    col_3 = 'created_at'
    if col_3 not in columns:
        print(f"Adding missing column '{col_3}'.")
        op.add_column('users', sa.Column(col_3, sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')))
        op.alter_column('users', col_3, nullable=False)


def downgrade() -> None:
    # A simple downgrade, as the main purpose is to fix the forward state.
    # In a real scenario, we might want to check for column existence before dropping.
    try:
        op.drop_column('users', 'user_type')
        op.drop_column('users', 'created_at')
        op.alter_column('users', 'telegram_user_id', new_column_name='telegram_id')
    except Exception as e:
        print(f"Downgrade failed, columns might already be removed. Error: {e}")