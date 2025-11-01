"""Drop legacy column 'payload_raw' from parsing_attempts safely."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# Revision identifiers
revision = "20251101_drop_payload_raw_column"
down_revision = "20251028_add_parsing_infra_fixed"  # عدلها حسب آخر ترحيل لديك
branch_labels = None
depends_on = None


def column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if column exists in a given table."""
    return conn.execute(
        text("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name=:tname AND column_name=:cname
        )
        """),
        {"tname": table_name, "cname": column_name},
    ).scalar()


def upgrade() -> None:
    conn = op.get_bind()

    # --- حذف العمود payload_raw بأمان إن وُجد ---
    if column_exists(conn, "parsing_attempts", "payload_raw"):
        # أولاً إزالة أي قيود محتملة قبل الحذف (اختياري)
        try:
            conn.execute(text("ALTER TABLE parsing_attempts ALTER COLUMN payload_raw DROP NOT NULL"))
        except Exception:
            pass  # إذا فشل، نتجاهل لأنها خطوة احترازية فقط

        op.drop_column("parsing_attempts", "payload_raw")
        conn.execute(text("COMMENT ON TABLE parsing_attempts IS 'payload_raw column dropped (legacy cleanup).'"))
        print("✅ Column 'payload_raw' dropped successfully.")
    else:
        print("ℹ️ Column 'payload_raw' not found; nothing to drop.")


def downgrade() -> None:
    conn = op.get_bind()

    # --- استعادة العمود في حال التراجع (Downgrade) ---
    if not column_exists(conn, "parsing_attempts", "payload_raw"):
        op.add_column(
            "parsing_attempts",
            sa.Column("payload_raw", sa.Text(), nullable=True)
        )
        conn.execute(text("COMMENT ON COLUMN parsing_attempts.payload_raw IS 'Restored legacy column (nullable).'"))