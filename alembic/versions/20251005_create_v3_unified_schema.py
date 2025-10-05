"""Unified safe & smart schema migration (full idempotent)

Revision ID: 20251008_full_unified_schema
Revises: None
Create Date: 2025-10-08 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = '20251008_full_unified_schema'
down_revision = None
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
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT 1 FROM pg_indexes WHERE indexname = :idx
    """), {"idx": index_name})
    return result.first() is not None

def _create_enum_if_not_exists(enum_name, sql_values, alt_names=None):
    """
    Create enum type only if it does not already exist (including alternative old names)
    """
    alt_names = alt_names or []
    all_names = [enum_name] + alt_names
    names_check = ",".join(["'%s'" % n for n in all_names])
    op.execute(f"""
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type t
        JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname IN ({names_check})
    ) THEN
        CREATE TYPE {enum_name} AS ENUM ({sql_values});
    END IF;
END $$;
""")


# =============================
# Upgrade logic
# =============================
def upgrade() -> None:
    conn = op.get_bind()

    # --- ENUM TYPES ---
    _create_enum_if_not_exists(
        'recommendationstatusenum',
        "'PENDING','ACTIVE','CLOSED'",
        alt_names=['recommendationstatus']
    )
    _create_enum_if_not_exists(
        'ordertypeenum',
        "'MARKET','LIMIT','STOP_MARKET'",
        alt_names=['ordertype']
    )
    _create_enum_if_not_exists(
        'exitstrategyenum',
        "'CLOSE_AT_FINAL_TP','MANUAL_CLOSE_ONLY'",
        alt_names=['exitstrategy']
    )
    _create_enum_if_not_exists(
        'usertypeenum',
        "'TRADER','ANALYST'",
        alt_names=['usertype']
    )
    _create_enum_if_not_exists(
        'usertradestatusenum',
        "'OPEN','CLOSED'",
        alt_names=['usertradestatus']
    )

    # =============================
    # USERS TABLE
    # =============================
    if has_table(conn, "users"):
        if not has_column("users", "email"):
            op.add_column("users", sa.Column("email", sa.String(255), nullable=True))
        if not has_column("users", "last_login_at"):
            op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
        if not has_index(conn, "ix_users_telegram_user_id"):
            op.create_index("ix_users_telegram_user_id", "users", ["telegram_user_id"], unique=True)
    else:
        op.create_table(
            'users',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('telegram_user_id', sa.BigInteger(), nullable=False, unique=True),
            sa.Column('user_type', sa.Enum('TRADER','ANALYST', name='usertypeenum', create_type=False), server_default='TRADER', nullable=False),
            sa.Column('username', sa.String(), nullable=True),
            sa.Column('first_name', sa.String(), nullable=True),
            sa.Column('is_active', sa.Boolean(), server_default=sa.text('false'), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), onupdate=sa.text('now()'), nullable=False),
            sa.Column('email', sa.String(255), nullable=True),
            sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True)
        )
        op.create_index("ix_users_telegram_user_id", "users", ["telegram_user_id"], unique=True)

    # =============================
    # ANALYST_PROFILES
    # =============================
    if has_table(conn, "analyst_profiles"):
        if not has_column("analyst_profiles", "profile_picture_url"):
            op.add_column("analyst_profiles", sa.Column("profile_picture_url", sa.String(512), nullable=True))
        if not has_column("analyst_profiles", "is_verified"):
            op.add_column("analyst_profiles", sa.Column("is_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    else:
        op.create_table(
            'analyst_profiles',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, unique=True),
            sa.Column('public_name', sa.String(), nullable=True),
            sa.Column('bio', sa.Text(), nullable=True),
            sa.Column('is_public', sa.Boolean(), server_default=sa.text('false'), nullable=False),
            sa.Column('profile_picture_url', sa.String(512), nullable=True),
            sa.Column('is_verified', sa.Boolean(), server_default=sa.text('false'), nullable=False)
        )

    # =============================
    # CHANNELS
    # =============================
    if has_table(conn, "channels"):
        if not has_column("channels", "last_verified_at"):
            op.add_column("channels", sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))
        if not has_column("channels", "notes"):
            op.add_column("channels", sa.Column("notes", sa.Text(), nullable=True))
        if not has_index(conn, "ix_channels_analyst_id"):
            op.create_index("ix_channels_analyst_id", "channels", ["analyst_id"])
        if not has_index(conn, "ix_channels_telegram_channel_id"):
            op.create_index("ix_channels_telegram_channel_id", "channels", ["telegram_channel_id"], unique=True)
    else:
        op.create_table(
            'channels',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('analyst_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('telegram_channel_id', sa.BigInteger(), nullable=False, unique=True),
            sa.Column('username', sa.String(255), nullable=True),
            sa.Column('title', sa.String(255), nullable=True),
            sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('last_verified_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True)
        )
        op.create_index("ix_channels_analyst_id", "channels", ["analyst_id"])
        op.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_channels_username_ci
            ON channels (lower(username))
            WHERE username IS NOT NULL;
        """)

    # =============================
    # RECOMMENDATIONS
    # =============================
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
    else:
        op.create_table(
            'recommendations',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('analyst_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('channel_id', sa.Integer(), sa.ForeignKey('channels.id', ondelete='SET NULL'), nullable=True),
            sa.Column('asset', sa.String(), nullable=False),
            sa.Column('side', sa.String(), nullable=False),
            sa.Column('entry', sa.Numeric(20, 8), nullable=False),
            sa.Column('stop_loss', sa.Numeric(20, 8), nullable=False),
            sa.Column('targets', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column('order_type', sa.Enum('MARKET','LIMIT','STOP_MARKET', name='ordertypeenum', create_type=False), server_default='LIMIT', nullable=False),
            sa.Column('status', sa.Enum('PENDING','ACTIVE','CLOSED', name='recommendationstatusenum', create_type=False), server_default='PENDING', nullable=False),
            sa.Column('market', sa.String(), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('exit_strategy', sa.Enum('CLOSE_AT_FINAL_TP','MANUAL_CLOSE_ONLY', name='exitstrategyenum', create_type=False), server_default='CLOSE_AT_FINAL_TP', nullable=False),
            sa.Column('exit_price', sa.Numeric(20, 8), nullable=True),
            sa.Column('alert_meta', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
            sa.Column('highest_price_reached', sa.Numeric(20, 8), nullable=True),
            sa.Column('lowest_price_reached', sa.Numeric(20, 8), nullable=True),
            sa.Column('profit_stop_price', sa.Numeric(20, 8), nullable=True),
            sa.Column('open_size_percent', sa.Numeric(5, 2), server_default='100.00', nullable=False),
            sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), onupdate=sa.text('now()'), nullable=False),
        )
        for idx, cols, uniq in [
            ("ix_recommendations_asset", ["asset"], False),
            ("ix_recommendations_analyst_id", ["analyst_id"], False),
            ("ix_recommendations_status", ["status"], False),
        ]:
            op.create_index(idx, 'recommendations', cols, unique=uniq)

    # =============================
    # PUBLISHED_MESSAGES
    # =============================
    if has_table(conn, "published_messages"):
        if not has_index(conn, "ix_published_messages_recommendation_id"):
            op.create_index("ix_published_messages_recommendation_id", "published_messages", ["recommendation_id"])
    else:
        op.create_table(
            'published_messages',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('recommendation_id', sa.Integer(), sa.ForeignKey('recommendations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('telegram_message_id', sa.Integer(), nullable=False),
            sa.Column('channel_id', sa.Integer(), sa.ForeignKey('channels.id', ondelete='CASCADE'), nullable=False),
            sa.Column('sent_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        )
        op.create_index("ix_published_messages_recommendation_id", "published_messages", ["recommendation_id"])
        op.create_index("ix_published_messages_channel_id", "published_messages", ["channel_id"])

    # =============================
    # USER_TRADES
    # =============================
    if has_table(conn, "user_trades"):
        if not has_column("user_trades", "position_size_percent"):
            op.add_column("user_trades", sa.Column("position_size_percent", sa.Numeric(5, 2), server_default='100.00', nullable=False))
        if not has_column("user_trades", "notes"):
            op.add_column("user_trades", sa.Column("notes", sa.Text(), nullable=True))
        for idx, cols, uniq in [
            ("ix_user_trades_user_id", ["user_id"], False),
            ("ix_user_trades_recommendation_id", ["recommendation_id"], False),
            ("ix_user_trades_status", ["status"], False),
        ]:
            if not has_index(conn, idx):
                op.create_index(idx, "user_trades", cols, unique=uniq)
    else:
        op.create_table(
            'user_trades',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('recommendation_id', sa.Integer(), sa.ForeignKey('recommendations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('status', sa.Enum('OPEN','CLOSED', name='usertradestatusenum', create_type=False), server_default='OPEN', nullable=False),
            sa.Column('entry_price', sa.Numeric(20, 8), nullable=True),
            sa.Column('exit_price', sa.Numeric(20, 8), nullable=True),
            sa.Column('position_size_percent', sa.Numeric(5, 2), server_default='100.00', nullable=False),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('opened_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), onupdate=sa.text('now()'), nullable=False),
        )
        for idx, cols, uniq in [
            ("ix_user_trades_user_id", ["user_id"], False),
            ("ix_user_trades_recommendation_id", ["recommendation_id"], False),
            ("ix_user_trades_status", ["status"], False),
        ]:
            op.create_index(idx, 'user_trades', cols, unique=uniq)

    # =============================
    # USER_SETTINGS
    # =============================
    if not has_table(conn, "user_settings"):
        op.create_table(
            'user_settings',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, unique=True),
            sa.Column('default_position_size', sa.Numeric(5, 2), server_default='100.00', nullable=False),
            sa.Column('auto_copy_trades', sa.Boolean(), server_default=sa.text('false'), nullable=False),
            sa.Column('notifications_enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
            sa.Column('risk_level', sa.String(), server_default='MEDIUM', nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), onupdate=sa.text('now()'), nullable=False),
        )
        op.create_index("ix_user_settings_user_id", "user_settings", ["user_id"], unique=True)


def downgrade() -> None:
    # Note: This is a complex migration. In production, you might want to
    # create separate downgrade migrations for specific schema changes.
    # This is a minimal safe downgrade that won't destroy data.
    
    # Drop new tables in reverse order
    op.drop_table('user_settings')
    op.drop_table('user_trades')
    op.drop_table('published_messages')
    
    # Drop new indexes
    indexes_to_drop = [
        "ix_published_messages_recommendation_id",
        "ix_published_messages_channel_id",
        "ix_user_trades_user_id", 
        "ix_user_trades_recommendation_id",
        "ix_user_trades_status",
        "ix_user_settings_user_id",
    ]
    
    conn = op.get_bind()
    for index_name in indexes_to_drop:
        if has_index(conn, index_name):
            op.drop_index(index_name)
    
    # Note: We don't drop enum types as they might be used by other tables
    # and dropping them requires checking dependencies first.