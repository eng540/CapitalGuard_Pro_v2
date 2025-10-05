"""Fix: idempotent ENUM creation + create_type=False for Unified v3.1

Revision ID: 20251006_fix_enum_idempotency_unified_v3_1
Revises: 
Create Date: 2025-10-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20251006_fix_enum_idempotency_unified_v3_1'
down_revision = None
branch_labels = None
depends_on = None


def _create_enum_if_not_exists(op, enum_name, sql_values, alt_names=None):
    """
    Helper: create an enum type only if it doesn't already exist under either enum_name
    or any of alt_names (to detect old names).
    - enum_name: target name to create (string)
    - sql_values: string content like "'PENDING','ACTIVE','CLOSED'"
    - alt_names: list of alternative typnames to check for existence (optional)
    """
    alt_names = alt_names or []
    all_names = [enum_name] + alt_names
    # Create a comma-separated quoted list for SQL check (not values)
    names_check = ",".join(["'%s'" % n for n in all_names])
    create_sql = f"""
DO $$ BEGIN
    -- if none of the candidate type names exist, create the canonical enum '{enum_name}'
    IF NOT EXISTS (
        SELECT 1 FROM pg_type t
        JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname IN ({names_check})
    ) THEN
        CREATE TYPE {enum_name} AS ENUM ({sql_values});
    END IF;
END $$;
"""
    op.execute(create_sql)


def upgrade() -> None:
    # =========================
    # Idempotent ENUM creation (check for old names too)
    # =========================
    # recommendation status: check both 'recommendationstatusenum' and older 'recommendationstatus'
    _create_enum_if_not_exists(
        op,
        enum_name='recommendationstatusenum',
        sql_values="'PENDING', 'ACTIVE', 'CLOSED'",
        alt_names=['recommendationstatus']
    )

    # order type
    _create_enum_if_not_exists(
        op,
        enum_name='ordertypeenum',
        sql_values="'MARKET', 'LIMIT', 'STOP_MARKET'",
        alt_names=['ordertype']
    )

    # exit strategy
    _create_enum_if_not_exists(
        op,
        enum_name='exitstrategyenum',
        sql_values="'CLOSE_AT_FINAL_TP', 'MANUAL_CLOSE_ONLY'",
        alt_names=['exitstrategy']
    )

    # user type (check both possible legacy names)
    _create_enum_if_not_exists(
        op,
        enum_name='usertypeenum',
        sql_values="'TRADER', 'ANALYST'",
        alt_names=['usertype']
    )

    # user trade status
    _create_enum_if_not_exists(
        op,
        enum_name='usertradestatusenum',
        sql_values="'OPEN', 'CLOSED'",
        alt_names=['usertradestatus']
    )

    # =========================
    # TABLES
    # Note: for sa.Enum columns we set create_type=False to avoid SQLAlchemy attempting
    # to create the enum type (we already created it above, idempotently).
    # =========================

    # USERS
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('telegram_user_id', sa.BigInteger(), nullable=False, unique=True, index=True),
        sa.Column('user_type', sa.Enum('TRADER', 'ANALYST', name='usertypeenum', create_type=False), server_default='TRADER', nullable=False),
        sa.Column('username', sa.String(), nullable=True),
        sa.Column('first_name', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), onupdate=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_users_telegram_user_id', 'users', ['telegram_user_id'], unique=True)

    # ANALYST PROFILES
    op.create_table('analyst_profiles',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('public_name', sa.String(), nullable=True),
        sa.Column('bio', sa.Text(), nullable=True),
        sa.Column('is_public', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )

    # CHANNELS
    op.create_table('channels',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('analyst_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('telegram_channel_id', sa.BigInteger(), nullable=False, unique=True),
        sa.Column('username', sa.String(255), nullable=True),
        sa.Column('title', sa.String(255), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
    )
    op.create_index('ix_channels_analyst_id', 'channels', ['analyst_id'])
    # keep case-insensitive unique username
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_channels_username_ci
        ON channels (lower(username))
        WHERE username IS NOT NULL;
    """)

    # RECOMMENDATIONS
    op.create_table('recommendations',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('analyst_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('channel_id', sa.Integer(), sa.ForeignKey('channels.id', ondelete='SET NULL'), nullable=True),
        sa.Column('asset', sa.String(), nullable=False),
        sa.Column('side', sa.String(), nullable=False),
        sa.Column('entry', sa.Numeric(20, 8), nullable=False),
        sa.Column('stop_loss', sa.Numeric(20, 8), nullable=False),
        sa.Column('targets', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('order_type', sa.Enum('MARKET', 'LIMIT', 'STOP_MARKET', name='ordertypeenum', create_type=False), server_default='LIMIT', nullable=False),
        sa.Column('status', sa.Enum('PENDING', 'ACTIVE', 'CLOSED', name='recommendationstatusenum', create_type=False), server_default='PENDING', nullable=False),
        sa.Column('market', sa.String(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('exit_strategy', sa.Enum('CLOSE_AT_FINAL_TP', 'MANUAL_CLOSE_ONLY', name='exitstrategyenum', create_type=False), server_default='CLOSE_AT_FINAL_TP', nullable=False),
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
    op.create_index('ix_recommendations_asset', 'recommendations', ['asset'])
    op.create_index('ix_recommendations_analyst_id', 'recommendations', ['analyst_id'])
    op.create_index('ix_recommendations_status', 'recommendations', ['status'])
    op.create_index('ix_recommendations_published_at', 'recommendations', ['published_at'])

    # PUBLISHED MESSAGES
    op.create_table('published_messages',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('recommendation_id', sa.Integer(), sa.ForeignKey('recommendations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('telegram_channel_id', sa.BigInteger(), nullable=False),
        sa.Column('telegram_message_id', sa.BigInteger(), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_published_messages_recommendation_id', 'published_messages', ['recommendation_id'])

    # RECOMMENDATION EVENTS
    op.create_table('recommendation_events',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('recommendation_id', sa.Integer(), sa.ForeignKey('recommendations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('event_type', sa.String(50), nullable=False),
        sa.Column('event_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('event_timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_recommendation_events_recommendation_id', 'recommendation_events', ['recommendation_id'])
    op.create_index('ix_recommendation_events_event_type', 'recommendation_events', ['event_type'])

    # SUBSCRIPTIONS
    op.create_table('subscriptions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('trader_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('analyst_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('start_date', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('end_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    )

    # USER TRADES
    op.create_table('user_trades',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('asset', sa.String(), nullable=False),
        sa.Column('side', sa.String(), nullable=False),
        sa.Column('entry', sa.Numeric(20, 8), nullable=False),
        sa.Column('stop_loss', sa.Numeric(20, 8), nullable=False),
        sa.Column('targets', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('status', sa.Enum('OPEN', 'CLOSED', name='usertradestatusenum', create_type=False), server_default='OPEN', nullable=False),
        sa.Column('close_price', sa.Numeric(20, 8), nullable=True),
        sa.Column('pnl_percentage', sa.Numeric(10, 4), nullable=True),
        sa.Column('source_recommendation_id', sa.Integer(), sa.ForeignKey('recommendations.id', ondelete='SET NULL'), nullable=True),
        sa.Column('source_forwarded_text', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_user_trades_user_id', 'user_trades', ['user_id'])
    op.create_index('ix_user_trades_source_recommendation_id', 'user_trades', ['source_recommendation_id'])
    op.create_index('ix_user_trades_status', 'user_trades', ['status'])

    # ANALYST STATS
    op.create_table('analyst_stats',
        sa.Column('analyst_profile_id', sa.Integer(), sa.ForeignKey('analyst_profiles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('win_rate', sa.Numeric(5, 2), nullable=True),
        sa.Column('total_pnl', sa.Numeric(10, 4), nullable=True),
        sa.Column('total_trades', sa.Integer(), nullable=True),
        sa.Column('last_updated', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )


def downgrade() -> None:
    # drop in reverse order
    op.drop_table('analyst_stats')
    op.drop_index('ix_user_trades_status', table_name='user_trades')
    op.drop_index('ix_user_trades_source_recommendation_id', table_name='user_trades')
    op.drop_index('ix_user_trades_user_id', table_name='user_trades')
    op.drop_table('user_trades')
    op.drop_table('subscriptions')
    op.drop_index('ix_recommendation_events_event_type', table_name='recommendation_events')
    op.drop_index('ix_recommendation_events_recommendation_id', table_name='recommendation_events')
    op.drop_table('recommendation_events')
    op.drop_index('ix_published_messages_recommendation_id', table_name='published_messages')
    op.drop_table('published_messages')
    op.drop_index('ix_recommendations_published_at', table_name='recommendations')
    op.drop_index('ix_recommendations_status', table_name='recommendations')
    op.drop_index('ix_recommendations_analyst_id', table_name='recommendations')
    op.drop_index('ix_recommendations_asset', table_name='recommendations')
    op.drop_table('recommendations')
    # channels index drop (username index was created with IF NOT EXISTS)
    op.drop_index('ix_channels_analyst_id', table_name='channels')
    op.drop_table('channels')
    op.drop_table('analyst_profiles')
    op.drop_index('ix_users_telegram_user_id', table_name='users')
    op.drop_table('users')

    # we avoid dropping enum types automatically here to be safer in environments where
    # enums may be shared or used by other revisions; manual cleanup can be performed if needed.
    # If you really want to drop, you can run:
    # op.execute("DROP TYPE IF EXISTS recommendationstatusenum;")
    # op.execute("DROP TYPE IF EXISTS ordertypeenum;")
    # op.execute("DROP TYPE IF EXISTS exitstrategyenum;")
    # op.execute("DROP TYPE IF EXISTS usertypeenum;")
    # op.execute("DROP TYPE IF EXISTS usertradestatusenum;")