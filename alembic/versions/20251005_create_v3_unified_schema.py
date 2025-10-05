"""Safe & smart schema migration (full verification for tables, columns, enums, and indexes)

Revision ID: 20251005_safe_schema_full_update
Revises: 20251005_create_v3_unified_schema
Create Date: 2025-10-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = '20251005_safe_schema_full_update'
down_revision = '20251005_create_v3_unified_schema'
branch_labels = None
depends_on = None


# =============================
# Helper utilities
# =============================
def has_table(conn, table_name: str) -> bool:
    return sa.inspect(conn).has_table(table_name)

def has_column(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = :t AND column_name = :c
    """), {"t": table_name, "c": column_name})
    return result.first() is not None

def has_index(conn, index_name: str) -> bool:
    result = conn.execute(sa.text("""
        SELECT 1 FROM pg_indexes WHERE indexname = :idx
    """), {"idx": index_name})
    return result.first() is not None

# =============================
# Upgrade logic
# =============================
def upgrade() -> None:
    conn = op.get_bind()

    # --- Ensure ENUM types exist ---
    enums = {
        'recommendationstatusenum': "ENUM('PENDING','ACTIVE','CLOSED')",
        'ordertypeenum': "ENUM('MARKET','LIMIT','STOP_MARKET')",
        'exitstrategyenum': "ENUM('CLOSE_AT_FINAL_TP','MANUAL_CLOSE_ONLY')",
        'usertype': "ENUM('TRADER','ANALYST')",
        'usertradestatus': "ENUM('OPEN','CLOSED')"
    }
    for enum_name, definition in enums.items():
        op.execute(sa.text(f"""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = '{enum_name}') THEN
                    CREATE TYPE {enum_name} AS {definition};
                END IF;
            END $$;
        """))

    # --- USERS TABLE ---
    if has_table(conn, "users"):
        if not has_column("users", "email"):
            op.add_column("users", sa.Column("email", sa.String(255), nullable=True))
        if not has_column("users", "last_login_at"):
            op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
        if not has_index(conn, "ix_users_telegram_user_id"):
            op.create_index("ix_users_telegram_user_id", "users", ["telegram_user_id"], unique=True)

    # --- ANALYST_PROFILES ---
    if has_table(conn, "analyst_profiles"):
        if not has_column("analyst_profiles", "profile_picture_url"):
            op.add_column("analyst_profiles", sa.Column("profile_picture_url", sa.String(512), nullable=True))
        if not has_column("analyst_profiles", "is_verified"):
            op.add_column("analyst_profiles", sa.Column("is_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False))

    # --- CHANNELS ---
    if has_table(conn, "channels"):
        if not has_column("channels", "last_verified_at"):
            op.add_column("channels", sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))
        if not has_column("channels", "notes"):
            op.add_column("channels", sa.Column("notes", sa.Text(), nullable=True))
        if not has_index(conn, "ix_channels_analyst_id"):
            op.create_index("ix_channels_analyst_id", "channels", ["analyst_id"])
        if not has_index(conn, "ix_channels_telegram_channel_id"):
            op.create_index("ix_channels_telegram_channel_id", "channels", ["telegram_channel_id"], unique=True)

    # --- RECOMMENDATIONS ---
    if has_table(conn, "recommendations"):
        new_columns = {
            "alert_meta": sa.Column("alert_meta", postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
            "highest_price_reached": sa.Column("highest_price_reached", sa.Numeric(20, 8), nullable=True),
            "lowest_price_reached": sa.Column("lowest_price_reached", sa.Numeric(20, 8), nullable=True),
            "profit_stop_price": sa.Column("profit_stop_price", sa.Numeric(20, 8), nullable=True),
        }
        for col_name, col_def in new_columns.items():
            if not has_column("recommendations", col_name):
                op.add_column("recommendations", col_def)

        for idx, cols, uniq in [
            ("ix_recommendations_asset", ["asset"], False),
            ("ix_recommendations_status", ["status"], False),
            ("ix_recommendations_analyst_id", ["analyst_id"], False),
        ]:
            if not has_index(conn, idx):
                op.create_index(idx, "recommendations", cols, unique=uniq)

    # --- PUBLISHED_MESSAGES ---
    if has_table(conn, "published_messages"):
        if not has_index(conn, "ix_published_messages_recommendation_id"):
            op.create_index("ix_published_messages_recommendation_id", "published_messages", ["recommendation_id"])

    # --- RECOMMENDATION_EVENTS ---
    if has_table(conn, "recommendation_events"):
        if not has_index(conn, "ix_recommendation_events_recommendation_id"):
            op.create_index("ix_recommendation_events_recommendation_id", "recommendation_events", ["recommendation_id"])
        if not has_index(conn, "ix_recommendation_events_event_type"):
            op.create_index("ix_recommendation_events_event_type", "recommendation_events", ["event_type"])

    # --- SUBSCRIPTIONS ---
    if has_table(conn, "subscriptions"):
        if not has_column("subscriptions", "renewal_count"):
            op.add_column("subscriptions", sa.Column("renewal_count", sa.Integer(), server_default='0', nullable=False))

    # --- USER_TRADES ---
    if has_table(conn, "user_trades"):
        extra_columns = {
            "exchange_name": sa.Column("exchange_name", sa.String(128), nullable=True),
            "risk_percent": sa.Column("risk_percent", sa.Numeric(5, 2), server_default='1.0', nullable=True),
        }
        for col_name, col_def in extra_columns.items():
            if not has_column("user_trades", col_name):
                op.add_column("user_trades", col_def)

        for idx, cols, uniq in [
            ("ix_user_trades_asset", ["asset"], False),
            ("ix_user_trades_user_id", ["user_id"], False),
            ("ix_user_trades_status", ["status"], False),
        ]:
            if not has_index(conn, idx):
                op.create_index(idx, "user_trades", cols, unique=uniq)

    # --- ANALYST_STATS ---
    if not has_table(conn, "analyst_stats"):
        op.create_table(
            'analyst_stats',
            sa.Column('analyst_profile_id', sa.Integer(), nullable=False),
            sa.Column('win_rate', sa.Numeric(precision=5, scale=2), nullable=True),
            sa.Column('total_pnl', sa.Numeric(precision=10, scale=4), nullable=True),
            sa.Column('total_trades', sa.Integer(), nullable=True),
            sa.Column('last_updated', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.ForeignKeyConstraint(['analyst_profile_id'], ['analyst_profiles.id'], ),
            sa.PrimaryKeyConstraint('analyst_profile_id')
        )


# =============================
# Downgrade
# =============================
def downgrade() -> None:
    # Safe downgrade does not drop anything
    pass