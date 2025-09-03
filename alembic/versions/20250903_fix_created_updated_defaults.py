# --- START OF FILE: alembic/versions/20250903_fix_created_updated_defaults.py ---
"""Ensure created_at/updated_at defaults & trigger

Revision ID: 20250903_fix_created_updated_defaults
Revises: 20250903_set_defaults_for_timestamps
Create Date: 2025-09-03 21:05:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20250903_fix_created_updated_defaults"
down_revision = "20250903_set_defaults_for_timestamps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) اجعل created_at/updated_at تملك DEFAULT now() على مستوى قاعدة البيانات
    #    (بدون تغيير نوع الحقل؛ سيستخدم now() التوقيت الزمني لـ Postgres).
    op.execute("ALTER TABLE recommendations ALTER COLUMN created_at SET DEFAULT now();")
    op.execute("ALTER TABLE recommendations ALTER COLUMN updated_at SET DEFAULT now();")

    # 2) عالج أي صفوف قديمة تملك NULL لضمان نجاح القيود.
    op.execute("UPDATE recommendations SET created_at = now() WHERE created_at IS NULL;")
    op.execute("UPDATE recommendations SET updated_at = now() WHERE updated_at IS NULL;")

    # 3) (اختياري-موصى به) أنشئ دالة ومُشغّل لتحديث updated_at تلقائياً عند UPDATE
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # احذف أي مُشغّل سابق بنفس الاسم لتجنّب التكرار
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_recommendations_set_updated_at ON recommendations;
        """
    )

    # أنشئ المُشغّل
    op.execute(
        """
        CREATE TRIGGER trg_recommendations_set_updated_at
        BEFORE UPDATE ON recommendations
        FOR EACH ROW
        EXECUTE FUNCTION set_updated_at();
        """
    )


def downgrade() -> None:
    # إزالة المُشغّل والدالة
    op.execute(
        "DROP TRIGGER IF EXISTS trg_recommendations_set_updated_at ON recommendations;"
    )
    op.execute("DROP FUNCTION IF EXISTS set_updated_at();")

    # التراجع عن DEFAULT (اترك الحقول كما كانت دون قيمة افتراضية)
    op.execute("ALTER TABLE recommendations ALTER COLUMN updated_at DROP DEFAULT;")
    op.execute("ALTER TABLE recommendations ALTER COLUMN created_at DROP DEFAULT;")
# --- END OF FILE ---