# --- START OF FILE: alembic/versions/20250829_add_message_meta.py ---
"""add message_id & published_at to recommendations

Revision ID: 20250829_add_message_meta
Revises: 20250828_change_uid
Create Date: 2025-08-29 12:15:00
"""

from alembic import op
import sqlalchemy as sa

revision = "20250829_add_message_meta"
down_revision = "20250828_change_uid"
branch_labels = None
depends_on = None

def upgrade() -> None:
    with op.batch_alter_table("recommendations") as batch_op:
        batch_op.add_column(sa.Column("message_id", sa.BigInteger(), nullable=True))
        # اختياري لكن أفضل: وعي المناطق الزمنية
        batch_op.add_column(sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))

def downgrade() -> None:
    with op.batch_alter_table("recommendations") as batch_op:
        batch_op.drop_column("published_at")
        batch_op.drop_column("message_id")
# --- END OF FILE ---