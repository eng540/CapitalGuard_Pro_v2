"""add message_id & published_at to recommendations

Revision ID: 20250829_add_message_meta
Revises: 20250828_change_user_id_to_string
Create Date: 2025-08-29 12:15:00
"""
from alembic import op
import sqlalchemy as sa

revision = "20250829_add_message_meta"
down_revision = "20250828_change_user_id_to_string"
branch_labels = None
depends_on = None

def upgrade() -> None:
    with op.batch_alter_table("recommendations") as batch_op:
        batch_op.add_column(sa.Column("message_id", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    # فهارس (اختياري):
    # op.create_index("ix_recs_message_id", "recommendations", ["message_id"], unique=False)
    # op.create_index("ix_recs_published_at", "recommendations", ["published_at"], unique=False)

def downgrade() -> None:
    # op.drop_index("ix_recs_published_at", table_name="recommendations")
    # op.drop_index("ix_recs_message_id", table_name="recommendations")
    with op.batch_alter_table("recommendations") as batch_op:
        batch_op.drop_column("published_at")
        batch_op.drop_column("message_id")