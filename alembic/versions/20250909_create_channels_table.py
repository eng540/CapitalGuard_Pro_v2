#// --- START: alembic/versions/20250909_create_channels_table.py ---
"""Create channels table for analyst broadcasting

Revision ID: 20250909_create_channels_table
Revises: 20250908_make_password_nullable
Create Date: 2025-09-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20250909_create_channels_table"
down_revision = "20250908_make_password_nullable"  # ⚠️ حدّثها لتطابق آخر ترحيل لديك
branch_labels = None
depends_on = None


def upgrade() -> None:
    # إنشاء جدول القنوات
    op.create_table(
        "channels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False, index=True),
        sa.Column("telegram_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_channel_id"),
        sa.UniqueConstraint("username"),
    )
    # فهارس مساعدة
    op.create_index(op.f("ix_channels_user_id"), "channels", ["user_id"], unique=False)
    op.create_index(op.f("ix_channels_telegram_channel_id"), "channels", ["telegram_channel_id"], unique=True)


def downgrade() -> None:
    # إسقاط الفهارس ثم الجدول
    op.drop_index(op.f("ix_channels_telegram_channel_id"), table_name="channels")
    op.drop_index(op.f("ix_channels_user_id"), table_name="channels")
    op.drop_table("channels")
#// --- END: alembic/versions/20250909_create_channels_table.py ---