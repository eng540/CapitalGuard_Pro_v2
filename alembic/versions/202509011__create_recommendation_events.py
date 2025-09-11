# --- START OF FILE: alembic/versions/20250911_safe_add_recommendation_events_and_tracking.py ---
"""Safely add recommendation_events table, tracking fields, and backfill CREATE events.

- Creates table `recommendation_events` if missing.
- Adds columns `highest_price_reached`, `lowest_price_reached` to `recommendations` if missing.
- Backfills one CREATE event per existing recommendation (skips if already exists).
- Works even if some expected columns in `recommendations` are absent (builds JSON dynamically).

NOTE: Replace `revision` and `down_revision` with your actual Alembic IDs.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.dialects import postgresql

# ========= Alembic IDs (EDIT ME) =========
revision = "a20250911_events_tracking_safe"        # ← ضع الـ revision الحقيقي هنا
down_revision = "202509010_1_create_tran_rec"      # ← تأكد أنه آخر ملف عندك
branch_labels = None
depends_on = None
# =========================================


def _table_exists(bind, table_name: str) -> bool:
    insp = sa.inspect(bind)
    return table_name in insp.get_table_names()


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns(table_name)}
    return column_name in cols


def _index_exists(bind, table_name: str, index_name: str) -> bool:
    insp = sa.inspect(bind)
    try:
        idxs = insp.get_indexes(table_name)
    except Exception:
        return False
    names = {i.get("name") for i in idxs if i.get("name")}
    return index_name in names


def upgrade() -> None:
    bind = op.get_bind()

    # 1) Create recommendation_events table if missing
    if not _table_exists(bind, "recommendation_events"):
        op.create_table(
            "recommendation_events",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("recommendation_id", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=50), nullable=False),
            sa.Column(
                "event_timestamp",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("event_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.ForeignKeyConstraint(
                ["recommendation_id"], ["recommendations.id"], ondelete="CASCADE"
            ),
        )

    # Create indexes if missing
    # Alembic op.create_index has no IF NOT EXISTS; we check manually
    if not _index_exists(bind, "recommendation_events", "ix_recommendation_events_recommendation_id"):
        op.create_index(
            "ix_recommendation_events_recommendation_id",
            "recommendation_events",
            ["recommendation_id"],
            unique=False,
        )
    if not _index_exists(bind, "recommendation_events", "ix_recommendation_events_event_type"):
        op.create_index(
            "ix_recommendation_events_event_type",
            "recommendation_events",
            ["event_type"],
            unique=False,
        )

    # 2) Add tracking columns to recommendations if missing
    if _table_exists(bind, "recommendations"):
        if not _column_exists(bind, "recommendations", "highest_price_reached"):
            with op.batch_alter_table("recommendations", schema=None) as batch_op:
                batch_op.add_column(sa.Column("highest_price_reached", sa.Float(), nullable=True))
        if not _column_exists(bind, "recommendations", "lowest_price_reached"):
            with op.batch_alter_table("recommendations", schema=None) as batch_op:
                batch_op.add_column(sa.Column("lowest_price_reached", sa.Float(), nullable=True))

    # 3) Backfill CREATE events (idempotent)
    # Build JSON dynamically from available columns to avoid errors on schema drift.
    if _table_exists(bind, "recommendations") and _table_exists(bind, "recommendation_events"):
        insp = sa.inspect(bind)
        rec_cols = {c["name"] for c in insp.get_columns("recommendations")}

        # Candidate fields for event_data
        json_fields = []
        if "entry" in rec_cols:
            json_fields.append(("entry", "r.entry"))
        if "stop_loss" in rec_cols:
            json_fields.append(("sl", "r.stop_loss"))
        if "targets" in rec_cols:
            # If targets is JSONB already, pass through; else cast to jsonb
            # We don't know the type here, safest route: cast text->jsonb if needed.
            json_fields.append(("targets", "r.targets"))

        # Fallback: if none of the fields exist, just create minimal event_data
        if not json_fields:
            event_data_expr = "jsonb_build_object('note','created by migration')"
        else:
            # Construct jsonb_build_object('k1', v1, 'k2', v2, ...)
            pairs = ", ".join([f"'{k}', {v}" for k, v in json_fields])
            event_data_expr = f"jsonb_build_object({pairs})"

        # Prefer r.created_at if available, otherwise now()
        ts_expr = "r.created_at" if "created_at" in rec_cols else "now()"

        # Insert one CREATE event per recommendation if missing
        sql = f"""
            INSERT INTO recommendation_events (recommendation_id, event_type, event_timestamp, event_data)
            SELECT
                r.id,
                'CREATE',
                {ts_expr},
                {event_data_expr}
            FROM recommendations r
            LEFT JOIN recommendation_events re
                   ON re.recommendation_id = r.id AND re.event_type = 'CREATE'
            WHERE re.id IS NULL;
        """
        op.execute(text(sql))


def downgrade() -> None:
    bind = op.get_bind()

    # Revert tracking columns if exist
    if _table_exists(bind, "recommendations"):
        if _column_exists(bind, "recommendations", "lowest_price_reached"):
            with op.batch_alter_table("recommendations", schema=None) as batch_op:
                batch_op.drop_column("lowest_price_reached")
        if _column_exists(bind, "recommendations", "highest_price_reached"):
            with op.batch_alter_table("recommendations", schema=None) as batch_op:
                batch_op.drop_column("highest_price_reached")

    # Drop indexes and table (guarded)
    if _table_exists(bind, "recommendation_events"):
        if _index_exists(bind, "recommendation_events", "ix_recommendation_events_recommendation_id"):
            op.drop_index("ix_recommendation_events_recommendation_id", table_name="recommendation_events")
        if _index_exists(bind, "recommendation_events", "ix_recommendation_events_event_type"):
            op.drop_index("ix_recommendation_events_event_type", table_name="recommendation_events")
        op.drop_table("recommendation_events")
# --- END OF FILE ---