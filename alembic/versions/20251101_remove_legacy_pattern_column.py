# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: migrations/20251101_remove_legacy_pattern_column.py ---
"""remove legacy 'pattern' column from parsing_templates if it exists (self-healing)"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = '20251101_remove_legacy_pattern_column'
down_revision = '20251101_extend_alembic_version_length''
branch_labels = None
depends_on = None


def column_exists(conn, table_name: str, column_name: str) -> bool:
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
    if column_exists(conn, "parsing_templates", "pattern"):
        op.drop_column("parsing_templates", "pattern")
        print("✅ Dropped legacy column 'pattern' from parsing_templates.")


def downgrade() -> None:
    conn = op.get_bind()
    if not column_exists(conn, "parsing_templates", "pattern"):
        op.add_column(
            "parsing_templates",
            sa.Column("pattern", sa.Text(), nullable=True)
        )
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: migrations/20251101_remove_legacy_pattern_column.py ---