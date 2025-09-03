# --- START OF FILE: alembic/versions/20250904_repair_chain_and_set_timestamp_defaults.py ---
"""Repair chain & enforce DEFAULT now() for created_at/updated_at; backfill NULLs

Revision ID: 20250904_repair_chain_and_set_timestamp_defaults
Revises: 20250903_add_order_type
Create Date: 2025-09-04 00:12:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20250904_repair_chain_and_set_timestamp_defaults"
down_revision = "20250903_add_order_type"  # ← عدّلها لرأسك الحالي إن لزم
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) تأكيد وجود الأعمدة (للأمان؛ عادة موجودة)
    #    لا نُنشئ الأعمدة هنا؛ نفترض أنها موجودة حسب النموذج/سوابق الترحيل.

    # 2) فرض DEFAULT now() بطريقة Alembic رسمية
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

    # 3) معالجة أي صفوف قديمة NULL
    op.execute("UPDATE recommendations SET created_at = now() WHERE created_at IS NULL;")
    op.execute("UPDATE recommendations SET updated_at = now() WHERE updated_at IS NULL;")

    # 4) دالة ومُشغِّل تحديث updated_at قبل UPDATE
    #    نحمي الإنشاء من التكرار عبر كتلة DO ... EXCEPTION
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE p.proname = 'set_updated_at'
                  AND n.nspname = current_schema()
            ) THEN
                CREATE FUNCTION set_updated_at()
                RETURNS TRIGGER AS $func$
                BEGIN
                    NEW.updated_at = now();
                    RETURN NEW;
                END;
                $func$ LANGUAGE plpgsql;
            END IF;
        END;
        $$;
        """
    )

    # PostgreSQL لا يدعم CREATE TRIGGER IF NOT EXISTS قبل PG14، فنستخدم DO..EXCEPTION
    op.execute(
        """
        DO $$
        BEGIN
            BEGIN
                CREATE TRIGGER trg_recommendations_set_updated_at
                BEFORE UPDATE ON recommendations
                FOR EACH ROW
                EXECUTE FUNCTION set_updated_at();
            EXCEPTION
                WHEN duplicate_object THEN
                    -- موجود مسبقًا، تجاهل
                    NULL;
            END;
        END;
        $$;
        """
    )


def downgrade() -> None:
    # التراجع: حذف المُشغّل والدالة إن وجدت، ثم إسقاط الـ DEFAULT
    op.execute(
        """
        DO $$
        BEGIN
            BEGIN
                DROP TRIGGER trg_recommendations_set_updated_at ON recommendations;
            EXCEPTION
                WHEN undefined_object THEN
                    NULL;
            END;

            BEGIN
                DROP FUNCTION set_updated_at();
            EXCEPTION
                WHEN undefined_function THEN
                    NULL;
            END;
        END;
        $$;
        """
    )

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