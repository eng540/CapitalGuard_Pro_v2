"""Make user password nullable and add first_name

Revision ID: 20250908_make_password_nullable
Revises: 20250907_multi_tenancy_final
Create Date: 2025-09-08 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = "20250908_make_password_nullable"
# ⚠️ تأكد أن هذا يطابق أحدث revision لديك فعليًا
down_revision = "20250907_multi_tenancy_final"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Step 1: Make hashed_password nullable ---
    # نستخدم batch_alter_table لضمان التوافق (SQLite/PG)
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "hashed_password",
            existing_type=sa.VARCHAR(),  # سيُتجاهل الطول إن لم يكن محددًا في الأصل
            nullable=True,
        )

    # --- Step 2: Add first_name if missing (idempotent) ---
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("users")}

    if "first_name" not in columns:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.add_column(sa.Column("first_name", sa.String(), nullable=True))


def downgrade() -> None:
    # إعادة الحالة كما كانت (مع تحقّق شرطي لتجنّب التعثّر)
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("users")}

    # أسقط first_name لو كان موجودًا
    if "first_name" in columns:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.drop_column("first_name")

    # اجعل hashed_password غير قابل لـ NULL (قد يفشل لو فيه NULLs)
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "hashed_password",
            existing_type=sa.VARCHAR(),
            nullable=False,
        )