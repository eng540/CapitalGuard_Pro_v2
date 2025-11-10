# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/db/migrations/versions/20251110_add_user_trade_events_safe.py ---
"""
R1-S1 HOTFIX 11C — Safe Migration for user_trade_events

✅ الهدف:
    - إنشاء جدول user_trade_events إن لم يكن موجودًا.
    - التحقق من الأعمدة والفهارس وإضافتها عند غيابها.
    - آمن ضد إعادة التشغيل أو ازدواج التنفيذ.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

# --- Revision metadata ---
revision = '20251110_add_user_trade_events_safe'
down_revision = '20251110_add_watchlist_layer'
branch_labels = None
depends_on = None

# --- Main Upgrade ---
def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    table_name = "user_trade_events"
    existing_tables = inspector.get_table_names()

    # 1. إنشاء الجدول إذا لم يكن موجودًا
    if table_name not in existing_tables:
        op.create_table(
            table_name,
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_trade_id', sa.Integer(), sa.ForeignKey('user_trades.id', ondelete='CASCADE'), nullable=False, index=True),
            sa.Column('event_type', sa.String(50), nullable=False, index=True),
            sa.Column('event_timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('event_data', sa.dialects.postgresql.JSONB, nullable=True),
        )
        print(f"[MIGRATION] Created new table: {table_name}")
    else:
        print(f"[MIGRATION] Table already exists: {table_name}. Verifying structure...")

        # 2. التحقق من الأعمدة وإضافة المفقود
        existing_columns = [col["name"] for col in inspector.get_columns(table_name)]
        required_columns = {
            "id": sa.Column('id', sa.Integer(), primary_key=True),
            "user_trade_id": sa.Column('user_trade_id', sa.Integer(), sa.ForeignKey('user_trades.id', ondelete='CASCADE'), nullable=False, index=True),
            "event_type": sa.Column('event_type', sa.String(50), nullable=False, index=True),
            "event_timestamp": sa.Column('event_timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            "event_data": sa.Column('event_data', sa.dialects.postgresql.JSONB, nullable=True),
        }

        for col_name, col_def in required_columns.items():
            if col_name not in existing_columns:
                op.add_column(table_name, col_def)
                print(f"[MIGRATION] Added missing column: {col_name}")

    # 3. التحقق من الفهارس
    indexes_query = text("SELECT indexname FROM pg_indexes WHERE tablename = :tname")
    existing_indexes = [row[0] for row in bind.execute(indexes_query, {"tname": table_name})]

    if 'ix_user_trade_events_user_trade_id' not in existing_indexes:
        op.create_index('ix_user_trade_events_user_trade_id', table_name, ['user_trade_id'])
        print("[MIGRATION] Created index: ix_user_trade_events_user_trade_id")

    if 'ix_user_trade_events_event_type' not in existing_indexes:
        op.create_index('ix_user_trade_events_event_type', table_name, ['event_type'])
        print("[MIGRATION] Created index: ix_user_trade_events_event_type")

    print("[MIGRATION] Verification completed successfully.")


# --- Safe Downgrade ---
def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_name = "user_trade_events"

    existing_tables = inspector.get_table_names()
    if table_name in existing_tables:
        print(f"[MIGRATION] Table {table_name} exists. Skipping drop for safety.")
    else:
        print(f"[MIGRATION] No table named {table_name}, nothing to downgrade.")
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/db/migrations/versions/20251110_add_user_trade_events_safe.py ---