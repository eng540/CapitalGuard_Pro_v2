# --- START OF FILE: alembic/versions/20250903_hotfix_created_updated_defaults.py ---
"""Hotfix: enforce DEFAULT now() for created_at/updated_at and backfill NULLs

Revision ID: 20250903_hotfix_created_updated_defaults
Revises: 20250903_fix_created_updated_defaults
Create Date: 2025-09-03 22:05:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20250903_hotfix_created_updated_defaults"
down_revision = "20250903_fix_created_updated_defaults"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # أجعل DEFAULT = now() بشكل صريح عبر alter_column
    op.alter_column(
        "recommendations",
        "created_at",
        server_default=sa.text("now()"),
        existing_type=sa.DateTime(),
        existing_nullable=False,
    )
    op.alter_column(
        "recommendations",
        "updated_at",
        server_default=sa.text("now()"),
        existing_type=sa.DateTime(),
        existing_nullable=False,
    )

    # عالج أي قيَم NULL حالية (لو وُجدت) لتجنّب NotNullViolation
    op.execute("UPDATE recommendations SET created_at = now() WHERE created_at IS NULL;")
    op.execute("UPDATE recommendations SET updated_at = now() WHERE updated_at IS NULL;")

    # ملاحظة: لا نلمس قيود NOT NULL؛ فقط نضمن أن الـ DEFAULT موجود وأن الصفوف القديمة مصحّحة.


def downgrade() -> None:
    # التراجع: إزالة الـ DEFAULT فقط (لا نغيّر NOT NULL)
    op.alter_column(
        "recommendations",
        "updated_at",
        server_default=None,
        existing_type=sa.DateTime(),
        existing_nullable=False,
    )
    op.alter_column(
        "recommendations",
        "created_at",
        server_default=None,
        existing_type=sa.DateTime(),
        existing_nullable=False,
    )
# --- END OF FILE ---