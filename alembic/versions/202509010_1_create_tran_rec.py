# --- START OF NEW DATA MIGRATION FILE ---
"""Assign ownership of old recommendations to primary user

Revision ID: <your_newly_generated_revision_id>
Revises: 202509010_1_create_piblished_data
Create Date: 202509010_1_create_tran_rec

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '202509010_1_create_tran_rec'  # ⚠️ استبدل هذا بالمعرف الجديد
down_revision = '202509010_1_create_piblished_data' # ⚠️ تأكد أن هذا هو ملف ترحيل البيانات السابق
branch_labels = None
depends_on = None

# The primary user's Telegram ID to assign old recommendations to.
PRIMARY_USER_TELEGRAM_ID = 6488214361


def upgrade() -> None:
    """
    Finds all recommendations with a NULL user_id and assigns them to the
    primary user identified by PRIMARY_USER_TELEGRAM_ID.
    """
    print(f"Attempting to assign ownership of orphaned recommendations to user with Telegram ID: {PRIMARY_USER_TELEGRAM_ID}...")
    
    # This SQL statement is safe to run multiple times. It will only update
    # recommendations that currently have no owner (user_id IS NULL).
    op.execute(f"""
        UPDATE recommendations
        SET user_id = (SELECT id FROM users WHERE telegram_user_id = {PRIMARY_USER_TELEGRAM_ID} LIMIT 1)
        WHERE user_id IS NULL;
    """)
    
    print("Orphaned recommendations assignment process complete.")


def downgrade() -> None:
    """
    Downgrading this operation is not straightforward as we don't know the
    original state. We will not perform any action on downgrade to prevent
    accidental data loss. The change is considered irreversible.
    """
    print("Downgrade for assigning ownership is a no-op. No changes made.")
    pass
# --- END OF NEW DATA MIGRATION FILE ---