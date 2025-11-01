"""add parsing infrastructure tables (fully self-healing version)"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import text

revision = '20251028_add_parsing_infra_fixed'
down_revision = '20251022_add_profit_stop_fields'
branch_labels = None
depends_on = None


def table_exists(conn, table_name: str) -> bool:
    """تحقق من وجود الجدول في قاعدة البيانات"""
    return conn.execute(
        text("SELECT to_regclass(:tname) IS NOT NULL"), {"tname": table_name}
    ).scalar()


def column_exists(conn, table_name: str, column_name: str) -> bool:
    """تحقق من وجود العمود في الجدول"""
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

    # --- إنشاء جدول parsing_templates ---
    if not table_exists(conn, "parsing_templates"):
        op.create_table(
            "parsing_templates",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("pattern_type", sa.String(length=50), server_default="regex", nullable=False),
            sa.Column("pattern_value", sa.Text(), nullable=False),
            sa.Column("analyst_id", sa.Integer(), nullable=True),
            sa.Column("is_public", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("version", sa.Integer(), server_default="1", nullable=False),
            sa.Column("confidence_score", sa.Numeric(precision=5, scale=2), nullable=True),
            sa.Column("user_correction_rate", sa.Numeric(precision=5, scale=2), nullable=True),
            sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["analyst_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id")
        )
    else:
        # التحقق من الأعمدة في parsing_templates
        required_columns_templates = {
            "pattern_type": sa.Column("pattern_type", sa.String(length=50), server_default="regex", nullable=False),
            "pattern_value": sa.Column("pattern_value", sa.Text(), nullable=False),
            "analyst_id": sa.Column("analyst_id", sa.Integer(), nullable=True),
            "is_public": sa.Column("is_public", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            "version": sa.Column("version", sa.Integer(), server_default="1", nullable=False),
            "confidence_score": sa.Column("confidence_score", sa.Numeric(precision=5, scale=2), nullable=True),
            "user_correction_rate": sa.Column("user_correction_rate", sa.Numeric(precision=5, scale=2), nullable=True),
            "stats": sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            "created_at": sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            "updated_at": sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        }

        for col_name, col_def in required_columns_templates.items():
            if not column_exists(conn, "parsing_templates", col_name):
                op.add_column("parsing_templates", col_def)

    # --- إنشاء جدول parsing_attempts ---
    if not table_exists(conn, "parsing_attempts"):
        op.create_table(
            "parsing_attempts",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("raw_content", sa.Text(), nullable=False),
            sa.Column("used_template_id", sa.Integer(), nullable=True),
            sa.Column("result_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("was_successful", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("was_corrected", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("corrections_diff", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("parser_path_used", sa.String(length=50), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["used_template_id"], ["parsing_templates.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id")
        )
    else:
        # التحقق من الأعمدة في parsing_attempts
        required_columns_attempts = {
            "user_id": sa.Column("user_id", sa.Integer(), nullable=False),
            "raw_content": sa.Column("raw_content", sa.Text(), nullable=True),
            "used_template_id": sa.Column("used_template_id", sa.Integer(), nullable=True),
            "result_data": sa.Column("result_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            "was_successful": sa.Column("was_successful", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            "was_corrected": sa.Column("was_corrected", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            "corrections_diff": sa.Column("corrections_diff", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            "latency_ms": sa.Column("latency_ms", sa.Integer(), nullable=True),
            "parser_path_used": sa.Column("parser_path_used", sa.String(length=50), nullable=True),
            "created_at": sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        }

        for col_name, col_def in required_columns_attempts.items():
            if not column_exists(conn, "parsing_attempts", col_name):
                op.add_column("parsing_attempts", col_def)


def downgrade() -> None:
    conn = op.get_bind()
    if table_exists(conn, "parsing_attempts"):
        op.drop_table("parsing_attempts")
    if table_exists(conn, "parsing_templates"):
        op.drop_table("parsing_templates")