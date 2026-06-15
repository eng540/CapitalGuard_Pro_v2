# --- START OF FIXED FILE: alembic/versions/20251110_add_watchlist_layer.py ---
"""
Add Watchlist/Activated portfolio layers and channel auditing schema.

✅ FIXED VERSION: Handles enum transitions and default values safely.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text, inspect

# Revision identifiers
revision = "20251110_add_watchlist_layer"
down_revision = "20251104_optimize_parsing_db_performance"
branch_labels = None
depends_on = None

NEW_ENUM_NAME = "usertradestatus"
NEW_ENUM_VALUES = ("WATCHLIST", "PENDING_ACTIVATION", "ACTIVATED", "CLOSED")


def _enum_exists(bind, name: str) -> bool:
    return bool(bind.execute(text("SELECT 1 FROM pg_type WHERE typname = :n"), {"n": name}).fetchone())


def _get_enum_labels(bind, name: str):
    q = text("""
        SELECT e.enumlabel FROM pg_enum e
        JOIN pg_type t ON e.enumtypid = t.oid
        WHERE t.typname = :n
        ORDER BY e.enumsortorder
    """)
    return [r[0] for r in bind.execute(q, {"n": name}).fetchall()]


def _add_enum_value_if_missing(bind, enum_name: str, value: str):
    try:
        bind.execute(text(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{value}'"))
    except Exception as e:
        print(f"Note: Could not add enum value {value}: {e}")


def _get_column_default(bind, table_name: str, column_name: str):
    result = bind.execute(text("""
        SELECT column_default 
        FROM information_schema.columns 
        WHERE table_name = :table AND column_name = :column
    """), {"table": table_name, "column": column_name}).fetchone()
    return result[0] if result else None


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    ut = "user_trades"

    # --- 1. Ensure watched_channels table exists and complete ---
    table = "watched_channels"
    expected_columns = {
        "id": sa.Column("id", sa.Integer, primary_key=True),
        "user_id": sa.Column("user_id", sa.Integer, nullable=False),
        "telegram_channel_id": sa.Column("telegram_channel_id", sa.BigInteger, nullable=False),
        "channel_title": sa.Column("channel_title", sa.String(255)),
        "is_active": sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        "created_at": sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        "updated_at": sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    }

    if table not in insp.get_table_names():
        op.create_table(
            table,
            *expected_columns.values(),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("user_id", "telegram_channel_id", name="uq_user_channel_watch"),
        )
        op.create_index("ix_watched_channels_user_id", table, ["user_id"])
        op.create_index("ix_watched_channels_telegram_channel_id", table, ["telegram_channel_id"])
    else:
        existing = {c["name"] for c in insp.get_columns(table)}
        for name, col in expected_columns.items():
            if name not in existing:
                op.add_column(table, col.copy())

    # --- 2. Ensure user_trades columns and FK exist ---
    cols = {c["name"] for c in insp.get_columns(ut)}

    if "watched_channel_id" not in cols:
        op.add_column(ut, sa.Column("watched_channel_id", sa.Integer))
    if "original_published_at" not in cols:
        op.add_column(ut, sa.Column("original_published_at", sa.DateTime(timezone=True)))
    if "activated_at" not in cols:
        op.add_column(ut, sa.Column("activated_at", sa.DateTime(timezone=True)))

    fk_names = {f["name"] for f in insp.get_foreign_keys(ut)}
    if "fk_user_trades_watched_channel" not in fk_names:
        op.create_foreign_key(
            "fk_user_trades_watched_channel",
            ut,
            table,
            ["watched_channel_id"],
            ["id"],
            ondelete="SET NULL",
        )

    indexes = {i["name"] for i in insp.get_indexes(ut)}
    if "ix_user_trades_watched_channel_id" not in indexes:
        op.create_index("ix_user_trades_watched_channel_id", ut, ["watched_channel_id"])

    # --- 3. HANDLE ENUM TRANSITION SAFELY ---
    
    # الخطوة 1: التحقق من العمود الحالي ونوعه
    current_status_col = next((c for c in insp.get_columns(ut) if c["name"] == "status"), None)
    if not current_status_col:
        raise Exception("Column 'status' does not exist in user_trades table")
    
    current_default = _get_column_default(bind, ut, "status")
    print(f"Current status default: {current_default}")
    
    # الخطوة 2: التعامل مع النوع ENUM
    if _enum_exists(bind, NEW_ENUM_NAME):
        # ENUM موجود - إضافة القيم المفقودة فقط
        enum_labels = _get_enum_labels(bind, NEW_ENUM_NAME)
        for value in NEW_ENUM_VALUES:
            if value not in enum_labels:
                _add_enum_value_if_missing(bind, NEW_ENUM_NAME, value)
        
        # تغيير القيمة الافتراضية إذا لم تكن صحيحة
        if current_default != "'WATCHLIST'::usertradestatus":
            print("Setting default value to 'WATCHLIST'")
            bind.execute(text("COMMIT"))  # تأكيد أي معاملة سابقة
            op.alter_column(ut, "status", server_default=text("'WATCHLIST'"))
    else:
        # ENUM غير موجود - إنشاؤه وتحويل العمود
        print(f"Creating new enum type: {NEW_ENUM_NAME}")
        bind.execute(text("COMMIT"))  # تأكيد أي معاملة سابقة
        
        # إنشاء النوع ENUM الجديد
        sa.Enum(*NEW_ENUM_VALUES, name=NEW_ENUM_NAME).create(bind)
        
        # تحويل العمود إلى النوع الجديد
        op.alter_column(
            ut,
            "status",
            type_=sa.Enum(*NEW_ENUM_VALUES, name=NEW_ENUM_NAME),
            postgresql_using=f"status::text::{NEW_ENUM_NAME}",
            server_default="WATCHLIST"
        )


def downgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    ut = "user_trades"
    wc = "watched_channels"

    # إعادة القيمة الافتراضية القديمة إذا كانت موجودة
    try:
        current_default = _get_column_default(bind, ut, "status")
        if current_default == "'WATCHLIST'::usertradestatus":
            op.alter_column(ut, "status", server_default=None)
    except Exception as e:
        print(f"Note: Could not reset default value: {e}")

    # Drop added columns safely
    for col in ["watched_channel_id", "original_published_at", "activated_at"]:
        if col in {c["name"] for c in insp.get_columns(ut)}:
            try:
                op.drop_column(ut, col)
            except Exception:
                pass  # تم إضافة المسافة البادئة هنا

    # Drop indexes and FKs safely
    for ix in ["ix_user_trades_watched_channel_id"]:
        if ix in {i["name"] for i in insp.get_indexes(ut)}:
            try:
                op.drop_index(ix, table_name=ut)
            except Exception:
                pass  # تم إضافة المسافة البادئة هنا

    if "fk_user_trades_watched_channel" in {f["name"] for f in insp.get_foreign_keys(ut)}:
        try:
            op.drop_constraint("fk_user_trades_watched_channel", ut, type_="foreignkey")
        except Exception:
            pass  # تم إضافة المسافة البادئة هنا

    # Drop watched_channels safely
    if wc in insp.get_table_names():
        for ix in ["ix_watched_channels_user_id", "ix_watched_channels_telegram_channel_id"]:
            if ix in {i["name"] for i in insp.get_indexes(wc)}:
                try:
                    op.drop_index(ix, table_name=wc)
                except Exception:
                    pass  # تم إضافة المسافة البادئة هنا
        try:
            op.drop_table(wc)
        except Exception:
            pass  # تم إضافة المسافة البادئة هنا
# --- END OF FIXED FILE ---