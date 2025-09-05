"""Consolidated fix for the users table to ensure all columns exist.

Revision ID: 20250906_04_consolidated_fix
Revises: 20250905_01_add_user_foundation
Create Date: 2025-09-06 02:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20250906_04_consolidated_fix'
down_revision = '20250905_01_add_user_foundation'
branch_labels = None
depends_on = None

def upgrade() -> None:
    """
    A comprehensive patch that inspects the 'users' table and ensures
    all required columns (telegram_user_id, user_type, created_at) exist
    with the correct names and types, adding or renaming them as necessary.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    
    if not inspector.has_table("users"):
        print("CRITICAL: 'users' table not found. The initial migration must run first.")
        return

    columns = [c.get('name') for c in inspector.get_columns('users')]
    print(f"Inspecting 'users' table. Found columns: {columns}")

    # --- Fix 1: telegram_user_id ---
    correct_col_1 = 'telegram_user_id'
    wrong_col_1 = 'telegram_id'
    if correct_col_1 not in columns:
        if wrong_col_1 in columns:
            print(f"Action: Renaming '{wrong_col_1}' to '{correct_col_1}'.")
            op.alter_column('users', wrong_col_1, new_column_name=correct_col_1)
        else:
            print(f"Action: Adding missing column '{correct_col_1}'.")
            op.add_column('users', sa.Column(correct_col_1, sa.BigInteger(), nullable=False, server_default=sa.text("0")))
            op.alter_column('users', correct_col_1, server_default=None)
    else:
        print(f"Check: Column '{correct_col_1}' is OK.")

    # --- Fix 2: user_type ---
    col_2 = 'user_type'
    if col_2 not in columns:
        print(f"Action: Adding missing column '{col_2}'.")
        op.add_column('users', sa.Column(col_2, sa.String(length=50), nullable=False, server_default='trader'))
        op.alter_column('users', col_2, server_default=None)
    else:
        print(f"Check: Column '{col_2}' is OK.")
        
    # --- Fix 3: created_at ---
    col_3 = 'created_at'
    if col_3 not in columns:
        print(f"Action: Adding missing column '{col_3}'.")
        op.add_column('users', sa.Column(col_3, sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')))
        op.alter_column('users', col_3, server_default=None)
    else:
        print(f"Check: Column '{col_3}' is OK.")

    print("Users table synchronization complete.")


def downgrade() -> None:
    # This is a fix-forward migration, downgrade is minimal.
    print("Downgrading consolidated_users_fix. No structural changes will be made.")
    pass