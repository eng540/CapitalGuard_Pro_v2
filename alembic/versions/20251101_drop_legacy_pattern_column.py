# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: migrations/versions/20251101_drop_legacy_pattern_column.py ---
"""remove legacy column 'pattern' from parsing_templates if it exists"""

from alembic import op
from sqlalchemy import text

# Revision identifiers
revision = "20251101_drop_legacy_pattern_column"
down_revision = "20251101_extend_alembic_version_length"
branch_labels = None
depends_on = None


def column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a specific column exists in the given table."""
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
    """Upgrade: drop the legacy 'pattern' column if present."""
    conn = op.get_bind()
    if column_exists(conn, "parsing_templates", "pattern"):
        op.drop_column("parsing_templates", "pattern")
        print("✅ Dropped legacy column 'pattern' from parsing_templates.")
    else:
        print("ℹ️ Column 'pattern' not found; skipping drop operation.")


def downgrade() -> None:
    """Downgrade: restore the 'pattern' column if needed."""
    conn = op.get_bind()
    if not column_exists(conn, "parsing_templates", "pattern"):
        op.add_column(
            "parsing_templates",
            sa.Column("pattern", sa.Text(), nullable=True)
        )
        print("🔁 Restored column 'pattern' to parsing_templates.")
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: migrations/versions/20251101_drop_legacy_pattern_column.py ---