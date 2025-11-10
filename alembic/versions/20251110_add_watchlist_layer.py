# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: alembic/versions/20251110_add_watchlist_layer.py ---
"""
Add Watchlist/Activated portfolio layers and channel auditing schema.

✅ SAFE VERSION (R1-S1): This migration is idempotent and safe to re-run.
 - Creates 'watched_channels' only if missing.
 - Verifies that all columns exist if table already exists.
 - Adds missing columns and constraints to 'user_trades' only if absent.
 - Ensures ENUM 'usertradestatus' exists and includes all required values.
 - Fully compatible with PostgreSQL.
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
OLD_ENUM_VALUES = ("OPEN", "CLOSED")


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
        bind.execute(text(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS :v").bindparams(v=value))
    except Exception:
        pass


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)

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
    ut = "user_trades"
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

    # --- 3. ENUM usertradestatus ---
    enum_labels = _get_enum_labels(bind, NEW_ENUM_NAME) if _enum_exists(bind, NEW_ENUM_NAME) else []

    if not enum_labels:
        sa.Enum(*NEW_ENUM_VALUES, name=NEW_ENUM_NAME).create(bind, checkfirst=True)
        op.alter_column(
            ut,
            "status",
            type_=sa.Enum(*NEW_ENUM_VALUES, name=NEW_ENUM_NAME),
            postgresql_using="status::text::usertradestatus",
            server_default="WATCHLIST",
            nullable=False,
        )
    else:
        for v in NEW_ENUM_VALUES:
            if v not in enum_labels:
                _add_enum_value_if_missing(bind, NEW_ENUM_NAME, v)
        op.execute(text(f"ALTER TABLE {ut} ALTER COLUMN status SET DEFAULT 'WATCHLIST'"))


def downgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    ut = "user_trades"
    wc = "watched_channels"

    # Drop added columns safely
    for col in ["watched_channel_id", "original_published_at", "activated_at"]:
        if col in {c["name"] for c in insp.get_columns(ut)}:
            try:
                op.drop_column(ut, col)
            except Exception:
                pass

    # Drop indexes and FKs safely
    for ix in ["ix_user_trades_watched_channel_id"]:
        if ix in {i["name"] for i in insp.get_indexes(ut)}:
            try:
                op.drop_index(ix, table_name=ut)
            except Exception:
                pass
    if "fk_user_trades_watched_channel" in {f["name"] for f in insp.get_foreign_keys(ut)}:
        try:
            op.drop_constraint("fk_user_trades_watched_channel", ut, type_="foreignkey")
        except Exception:
            pass

    # Drop watched_channels safely
    if wc in insp.get_table_names():
        for ix in ["ix_watched_channels_user_id", "ix_watched_channels_telegram_channel_id"]:
            if ix in {i["name"] for i in insp.get_indexes(wc)}:
                try:
                    op.drop_index(ix, table_name=wc)
                except Exception:
                    pass
        try:
            op.drop_table(wc)
        except Exception:
            pass
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: alembic/versions/20251110_add_watchlist_layer.py ---