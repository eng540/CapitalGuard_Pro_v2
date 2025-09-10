# --- START OF NEW DATA MIGRATION FILE ---
"""Migrate legacy publication data to published_messages table

Revision ID: <your_newly_generated_revision_id>
Revises: 20250909_1_create_piblished_message
Create Date: 202509010_1_create_piblished_data

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '202509010_1_create_piblished_data' # ⚠️ استبدل هذا بالمعرف الجديد
down_revision = '20250909_1_create_piblished_message' # ⚠️ تأكد أن هذا هو ملف الترحيل السابق
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Migrates the old publication data from the `recommendations` table
    to the new `published_messages` table.
    """
    print("Starting data migration for legacy publication info...")
    
    # This SQL statement inserts records into the new table by selecting
    # from the old table. It cleverly avoids inserting duplicates if a
    # record for a specific recommendation_id already exists in the new table.
    op.execute("""
        INSERT INTO published_messages (recommendation_id, telegram_channel_id, telegram_message_id, published_at)
        SELECT 
            r.id, 
            r.channel_id, 
            r.message_id, 
            COALESCE(r.published_at, r.created_at, NOW())
        FROM 
            recommendations AS r
        LEFT JOIN
            published_messages AS pm ON r.id = pm.recommendation_id
        WHERE 
            r.channel_id IS NOT NULL 
            AND r.message_id IS NOT NULL
            AND pm.id IS NULL;
    """)
    
    print("Data migration for legacy publication info is complete.")


def downgrade() -> None:
    """
    Downgrading this migration does nothing, as reversing the data transfer
    is not necessary and could be destructive. We leave the migrated data as is.
    """
    print("Downgrade for data migration is a no-op. No changes made.")
    pass
# --- END OF NEW DATA MIGRATION FILE ---