# alembic/versions/20251008_full_unified_schema_v2.py (FINAL & ROBUST)
"""Unified safe & smart schema migration (full idempotent)

Revision ID: 20251008_full_unified_schema_v2
Revises: None
Create Date: 2025-10-08 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect, text

# revision identifiers
revision = '20251008_full_unified_schema_v2'
down_revision = None
branch_labels = None
depends_on = None


# =============================
# Enhanced Helper utilities
# =============================
def get_connection():
    """Get database connection safely"""
    return op.get_bind()

def table_exists(table_name: str) -> bool:
    """Check if table exists"""
    conn = get_connection()
    return inspect(conn).has_table(table_name)

def column_exists(table_name: str, column_name: str) -> bool:
    """Check if column exists in table"""
    if not table_exists(table_name):
        return False
    
    conn = get_connection()
    result = conn.execute(
        text("""
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = :table_name AND column_name = :column_name
        """),
        {"table_name": table_name, "column_name": column_name}
    )
    return result.first() is not None

def index_exists(index_name: str) -> bool:
    """Check if index exists"""
    conn = get_connection()
    result = conn.execute(
        text("SELECT 1 FROM pg_indexes WHERE indexname = :index_name"),
        {"index_name": index_name}
    )
    return result.first() is not None

def foreign_key_exists(table_name: str, column_name: str) -> bool:
    """Check if foreign key constraint exists"""
    if not table_exists(table_name) or not column_exists(table_name, column_name):
        return False
    
    conn = get_connection()
    result = conn.execute(
        text("""
            SELECT 1 FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
            WHERE tc.table_name = :table_name 
                AND kcu.column_name = :column_name
                AND tc.constraint_type = 'FOREIGN KEY'
        """),
        {"table_name": table_name, "column_name": column_name}
    )
    return result.first() is not None

def enum_type_exists(enum_name: str) -> bool:
    """Check if enum type exists"""
    conn = get_connection()
    result = conn.execute(
        text("SELECT 1 FROM pg_type WHERE typname = :enum_name"),
        {"enum_name": enum_name}
    )
    return result.first() is not None

def create_enum_safe(enum_name: str, values: list):
    """Safely create enum type if it doesn't exist"""
    if enum_type_exists(enum_name):
        return
    
    values_sql = ", ".join([f"'{value}'" for value in values])
    op.execute(f"CREATE TYPE {enum_name} AS ENUM ({values_sql})")

def safe_add_column(table_name: str, column_def, check_first=True):
    """Safely add column if it doesn't exist"""
    if check_first and column_exists(table_name, column_def.name):
        return False
    
    try:
        op.add_column(table_name, column_def)
        return True
    except Exception as e:
        print(f"Warning: Could not add column {column_def.name} to {table_name}: {e}")
        return False

def safe_create_index(index_name: str, table_name: str, columns: list, unique=False):
    """Safely create index if it doesn't exist and columns exist"""
    if index_exists(index_name):
        return False
    
    for column in columns:
        if not column_exists(table_name, column):
            print(f"Warning: Column {column} not found in {table_name}, skipping index {index_name}")
            return False
    
    try:
        op.create_index(index_name, table_name, columns, unique=unique)
        return True
    except Exception as e:
        print(f"Warning: Could not create index {index_name}: {e}")
        return False

def safe_create_foreign_key(table_name: str, column_name: str, target_table: str, target_column: str = 'id'):
    """Safely create foreign key constraint"""
    if foreign_key_exists(table_name, column_name):
        return False
    
    if not table_exists(target_table):
        print(f"Warning: Target table {target_table} does not exist, skipping foreign key")
        return False
    
    if not column_exists(table_name, column_name):
        print(f"Warning: Column {column_name} does not exist in {table_name}, skipping foreign key")
        return False
    
    constraint_name = f"fk_{table_name}_{column_name}_{target_table}"
    
    try:
        op.create_foreign_key(
            constraint_name,
            table_name,
            target_table,
            [column_name],
            [target_column],
            ondelete="CASCADE"
        )
        return True
    except Exception as e:
        print(f"Warning: Could not create foreign key {constraint_name}: {e}")
        return False


# =============================
# Upgrade logic
# =============================
def upgrade() -> None:
    conn = get_connection()
    
    print("Starting safe schema migration for v3.0...")

    # --- ENUM TYPES ---
    print("Creating enum types...")
    enums_to_create = {
        'recommendationstatusenum': ['PENDING', 'ACTIVE', 'CLOSED'],
        'ordertypeenum': ['MARKET', 'LIMIT', 'STOP_MARKET'],
        'exitstrategyenum': ['CLOSE_AT_FINAL_TP', 'MANUAL_CLOSE_ONLY'],
        'usertypeenum': ['TRADER', 'ANALYST'],
        'usertradestatusenum': ['OPEN', 'CLOSED']
    }
    
    for enum_name, values in enums_to_create.items():
        create_enum_safe(enum_name, values)

    # =============================
    # USERS TABLE
    # =============================
    print("Setting up USERS table...")
    if not table_exists("users"):
        op.create_table(
            'users',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('telegram_user_id', sa.BigInteger(), nullable=False),
            sa.Column('user_type', sa.Enum('TRADER', 'ANALYST', name='usertypeenum'), server_default='TRADER', nullable=False),
            sa.Column('username', sa.String(255), nullable=True),
            sa.Column('first_name', sa.String(255), nullable=True),
            sa.Column('is_active', sa.Boolean(), server_default=sa.text('false'), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), onupdate=sa.text('now()'), nullable=False),
        )
    safe_create_index("ix_users_telegram_user_id", "users", ["telegram_user_id"], unique=True)

    # =============================
    # ANALYST_PROFILES TABLE
    # =============================
    print("Setting up ANALYST_PROFILES table...")
    if not table_exists("analyst_profiles"):
        op.create_table(
            'analyst_profiles',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('user_id', sa.Integer(), nullable=False, unique=True),
            sa.Column('public_name', sa.String(255), nullable=True),
            sa.Column('bio', sa.Text(), nullable=True),
            sa.Column('is_public', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        )
    safe_create_foreign_key("analyst_profiles", "user_id", "users")

    # =============================
    # CHANNELS TABLE
    # =============================
    print("Setting up CHANNELS table...")
    if not table_exists("channels"):
        op.create_table(
            'channels',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('analyst_id', sa.Integer(), nullable=False),
            sa.Column('telegram_channel_id', sa.BigInteger(), nullable=False),
            sa.Column('username', sa.String(255), nullable=True),
            sa.Column('title', sa.String(255), nullable=True),
            sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        )
    safe_create_index("ix_channels_analyst_id", "channels", ["analyst_id"])
    safe_create_index("ix_channels_telegram_channel_id", "channels", ["telegram_channel_id"], unique=True)
    safe_create_foreign_key("channels", "analyst_id", "users")

    # =============================
    # RECOMMENDATIONS TABLE
    # =============================
    print("Setting up RECOMMENDATIONS table...")
    if not table_exists("recommendations"):
        op.create_table(
            'recommendations',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('analyst_id', sa.Integer(), nullable=False),
            sa.Column('channel_id', sa.Integer(), nullable=True),
            sa.Column('asset', sa.String(100), nullable=False),
            sa.Column('side', sa.String(50), nullable=False),
            sa.Column('entry', sa.Numeric(20, 8), nullable=False),
            sa.Column('stop_loss', sa.Numeric(20, 8), nullable=False),
            sa.Column('targets', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column('order_type', sa.Enum('MARKET', 'LIMIT', 'STOP_MARKET', name='ordertypeenum'), server_default='LIMIT', nullable=False),
            sa.Column('status', sa.Enum('PENDING', 'ACTIVE', 'CLOSED', name='recommendationstatusenum'), server_default='PENDING', nullable=False),
            sa.Column('market', sa.String(50), nullable=True, server_default='Futures'),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('exit_strategy', sa.Enum('CLOSE_AT_FINAL_TP', 'MANUAL_CLOSE_ONLY', name='exitstrategyenum'), server_default='CLOSE_AT_FINAL_TP', nullable=False),
            sa.Column('exit_price', sa.Numeric(20, 8), nullable=True),
            sa.Column('open_size_percent', sa.Numeric(5, 2), server_default='100.00', nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), onupdate=sa.text('now()'), nullable=False),
        )
    safe_create_index("ix_recommendations_asset", "recommendations", ["asset"])
    safe_create_index("ix_recommendations_status", "recommendations", ["status"])
    safe_create_index("ix_recommendations_analyst_id", "recommendations", ["analyst_id"])
    safe_create_foreign_key("recommendations", "analyst_id", "users")
    safe_create_foreign_key("recommendations", "channel_id", "channels")

    # =============================
    # USER_TRADES TABLE
    # =============================
    print("Setting up USER_TRADES table...")
    if not table_exists("user_trades"):
        op.create_table(
            'user_trades',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('source_recommendation_id', sa.Integer(), nullable=False),
            sa.Column('asset', sa.String(100), nullable=False),
            sa.Column('side', sa.String(50), nullable=False),
            sa.Column('entry', sa.Numeric(20, 8), nullable=False),
            sa.Column('stop_loss', sa.Numeric(20, 8), nullable=False),
            sa.Column('targets', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column('status', sa.Enum('OPEN', 'CLOSED', name='usertradestatusenum'), server_default='OPEN', nullable=False),
            sa.Column('close_price', sa.Numeric(20, 8), nullable=True),
            sa.Column('pnl_percentage', sa.Numeric(10, 4), nullable=True),
            sa.Column('source_forwarded_text', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        )
    safe_create_index("ix_user_trades_user_id", "user_trades", ["user_id"])
    safe_create_index("ix_user_trades_status", "user_trades", ["status"])
    safe_create_index("ix_user_trades_source_recommendation_id", "user_trades", ["source_recommendation_id"])
    safe_create_foreign_key("user_trades", "user_id", "users")
    safe_create_foreign_key("user_trades", "source_recommendation_id", "recommendations")

    # =============================
    # OTHER TABLES
    # =============================
    print("Setting up other supporting tables...")
    if not table_exists("published_messages"):
        op.create_table(
            'published_messages',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('recommendation_id', sa.Integer(), nullable=False),
            sa.Column('telegram_channel_id', sa.BigInteger(), nullable=False),
            sa.Column('telegram_message_id', sa.BigInteger(), nullable=False),
        )
    safe_create_index("ix_published_messages_recommendation_id", "published_messages", ["recommendation_id"])
    safe_create_foreign_key("published_messages", "recommendation_id", "recommendations")

    if not table_exists("recommendation_events"):
        op.create_table(
            'recommendation_events',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('recommendation_id', sa.Integer(), nullable=False),
            sa.Column('event_type', sa.String(50), nullable=False),
            sa.Column('event_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column('event_timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        )
    safe_create_index("ix_recommendation_events_recommendation_id", "recommendation_events", ["recommendation_id"])
    safe_create_foreign_key("recommendation_events", "recommendation_id", "recommendations")

    if not table_exists("subscriptions"):
        op.create_table(
            'subscriptions',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('trader_user_id', sa.Integer(), nullable=False),
            sa.Column('analyst_user_id', sa.Integer(), nullable=False),
            sa.Column('start_date', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('end_date', sa.DateTime(timezone=True), nullable=True),
            sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        )
    safe_create_foreign_key("subscriptions", "trader_user_id", "users")
    safe_create_foreign_key("subscriptions", "analyst_user_id", "users")

    if not table_exists("analyst_stats"):
        op.create_table(
            'analyst_stats',
            sa.Column('analyst_profile_id', sa.Integer(), primary_key=True),
            sa.Column('win_rate', sa.Numeric(5, 2), nullable=True),
            sa.Column('total_pnl', sa.Numeric(10, 4), nullable=True),
            sa.Column('total_trades', sa.Integer(), server_default='0', nullable=False),
            sa.Column('last_updated', sa.DateTime(timezone=True), server_default=sa.text('now()'), onupdate=sa.text('now()'), nullable=False),
        )
    safe_create_foreign_key("analyst_stats", "analyst_profile_id", "analyst_profiles")

    print("Schema migration completed successfully!")


def downgrade() -> None:
    """Safe downgrade - drops all tables and enums."""
    print("Starting full downgrade...")
    
    tables_to_drop = [
        'analyst_stats', 'subscriptions', 'recommendation_events', 
        'published_messages', 'user_trades', 'recommendations', 
        'channels', 'analyst_profiles', 'users'
    ]
    
    for table_name in tables_to_drop:
        if table_exists(table_name):
            op.drop_table(table_name)
    
    enum_types = [
        'usertradestatusenum', 'usertypeenum', 'exitstrategyenum',
        'ordertypeenum', 'recommendationstatusenum'
    ]
    
    for enum_type in enum_types:
        if enum_type_exists(enum_type):
            op.execute(f'DROP TYPE IF EXISTS {enum_type}')
    
    print("Downgrade completed.")