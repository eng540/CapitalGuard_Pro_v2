"""fix stuck shadow trades

Revision ID: 20251130_fix_stuck_shadow
Revises: 20251119_add_status_constraints
Create Date: 2025-11-30 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text
#20251119_add_status_constraints
# revision identifiers, used by Alembic.
revision = '20251130_fix_stuck_shadow'
# تأكد أن هذا المعرف يطابق آخر ملف ترحيل لديك في المجلد
down_revision = '20251119_add_status_constraints' 
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    
    # 1. تنفيذ عملية الإصلاح
    # نستهدف فقط الصفقات النشطة أو المعلقة التي علقت في وضع الظل
    result = conn.execute(text("""
        UPDATE recommendations
        SET is_shadow = false
        WHERE is_shadow = true 
        AND status IN ('PENDING', 'ACTIVE');
    """))
    
    # طباعة عدد الصفوف المتأثرة (للتأكيد في السجلات)
    print(f"✅ FIXED: Un-shadowed {result.rowcount} stuck trades.")


def downgrade() -> None:
    # لا نقوم بالتراجع عن هذا الإصلاح لأنه تصحيح لبيانات فاسدة
    pass