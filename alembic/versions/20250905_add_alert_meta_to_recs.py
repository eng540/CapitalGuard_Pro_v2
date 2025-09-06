# --- START OF FILE: alembic/versions/20250905_add_alert_meta_to_recs.py ---
"""Add alert_meta JSONB column to recommendations (idempotent)

Revision ID: 20250905_add_alert_meta
Revises: 20250904_repair_chain_and_set_timestamp_defaults
Create Date: 2025-09-05 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20250905_add_alert_meta"
down_revision = "20250904_repair_chain_and_set_timestamp_defaults"
branch_labels = None
depends_on = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    insp = sa.inspect(bind)
    cols = [c["name"] for c in insp.get_columns(table_name)]
    return column_name in cols


def upgrade() -> None:
    """
    Ensure JSONB alert_meta column exists and is NOT NULL with '{}'::jsonb default.
    Safe to run multiple times.
    """
    bind = op.get_bind()
    table = "recommendations"
    col = "alert_meta"
    default_expr = "'{}'::jsonb"

    if not _has_column(bind, table, col):
        # 1) أضف العمود بشكل قابل للتشغيل المتكرر
        op.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN IF NOT EXISTS {col} JSONB"
        )

    # 2) املأ أي صفوف NULL بالقيمة الافتراضية
    op.execute(
        f"UPDATE {table} SET {col} = {default_expr} WHERE {col} IS NULL"
    )

    # 3) اضبط DEFAULT و NOT NULL (idempotent)
    # ملاحظة: alter_column بـ server_default يعمل على Postgres،
    # ونستدعيه حتى لو كان مضبوطًا مسبقًا لأنه آمن.
    op.alter_column(
        table,
        col,
        existing_type=postgresql.JSONB(),
        server_default=sa.text(default_expr),
        existing_nullable=True,
    )
    # فرض NOT NULL (إذا كان مفروضًا مسبقًا سيبقى كما هو)
    op.alter_column(
        table,
        col,
        existing_type=postgresql.JSONB(),
        nullable=False,
    )


def downgrade() -> None:
    """
    Drop the column safely if present.
    """
    table = "recommendations"
    col = "alert_meta"
    # استخدام SQL مباشر مع IF EXISTS لتجنب أي خطأ إن لم يكن العمود موجودًا
    op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col}")
# --- END OF FILE ---