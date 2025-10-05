"""Unified Clean Schema v3.1

Revision ID: 20251005_unified_v3_1
Revises:
Create Date: 2025-10-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20251005_unified_v3_1'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================
    # ENUM Types (Safe creation)
    # =========================
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'recommendationstatusenum') THEN
                CREATE TYPE recommendationstatusenum AS ENUM ('PENDING', 'ACTIVE', 'CLOSED');
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'ordertypeenum') THEN
                CREATE TYPE ordertypeenum AS ENUM ('MARKET', 'LIMIT', 'STOP_MARKET');
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'exitstrategyenum') THEN
                CREATE TYPE exitstrategyenum AS ENUM ('CLOSE_AT_FINAL_TP', 'MANUAL_CLOSE_ONLY');
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'usertype') THEN
                CREATE TYPE usertype AS ENUM ('TRADER', 'ANALYST');
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'usertradestatus') THEN
                CREATE TYPE usertradestatus AS ENUM ('OPEN', 'CLOSED');
            END IF;
        END $$;
    """)

    # =========================
    # USERS
    # =========================
    op.create_table('users',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('telegram_user_id', sa.BigInteger(), nullable=False, unique=True, index=True),
        sa.Column('user_type', sa.Enum('TRADER', 'ANALYST', name='usertype'), server_default='TRADER', nullable=False),
        sa.Column('username', sa.String(), nullable=True),
        sa.Column('first_name', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), onupdate=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_users_telegram_user_id', 'users', ['telegram_user_id'], unique=True)

    # =========================
    # ANALYST PROFILES
    # =========================
    op.create_table('analyst_profiles',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('public_name', sa.String(), nullable=True),
        sa.Column('bio', sa.Text(), nullable=True),
        sa.Column('is_public', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )

    # =========================
    # CHANNELS
    # =========================
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
    op.execute("""
        CREATE UNIQUE INDEX uq_channels_username_ci
        ON channels (lower(username))
        WHERE username IS NOT NULL;
    """)

    # =========================
    # RECOMMENDATIONS
    # =========================
    op.create_table('recommendations',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('analyst_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('channel_id', sa.Integer(), sa.ForeignKey('channels.id', ondelete='SET NULL'), nullable=True),
        sa.Column('asset', sa.String(), nullable=False),
        sa.Column('side', sa.String(), nullable=False),
        sa.Column('entry', sa.Numeric(20, 8), nullable=False),
        sa.Column('stop_loss', sa.Numeric(20, 8), nullable=False),
        sa.Column('targets', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('order_type', sa.Enum('MARKET', 'LIMIT', 'STOP_MARKET', name='ordertypeenum'), server_default='LIMIT', nullable=False),
        sa.Column('status', sa.Enum('PENDING', 'ACTIVE', 'CLOSED', name='recommendationstatusenum'), server_default='PENDING', nullable=False),
        sa.Column('market', sa.String(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('exit_strategy', sa.Enum('CLOSE_AT_FINAL_TP', 'MANUAL_CLOSE_ONLY', name='exitstrategyenum'), server_default='CLOSE_AT_FINAL_TP', nullable=False),
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

    # =========================
    # PUBLISHED MESSAGES
    # =========================
    op.create_table('published_messages',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('recommendation_id', sa.Integer(), sa.ForeignKey('recommendations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('telegram_channel_id', sa.BigInteger(), nullable=False),
        sa.Column('telegram_message_id', sa.BigInteger(), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_published_messages_recommendation_id', 'published_messages', ['recommendation_id'])

    # =========================
    # RECOMMENDATION EVENTS
    # =========================
    op.create_table('recommendation_events',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('recommendation_id', sa.Integer(), sa.ForeignKey('recommendations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('event_type', sa.String(50), nullable=False),
        sa.Column('event_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('event_timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_recommendation_events_recommendation_id', 'recommendation_events', ['recommendation_id'])
    op.create_index('ix_recommendation_events_event_type', 'recommendation_events', ['event_type'])

    # =========================
    # SUBSCRIPTIONS
    # =========================
    op.create_table('subscriptions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('trader_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('analyst_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('start_date', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('end_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    )

    # =========================
    # USER TRADES
    # =========================
    op.create_table('user_trades',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('asset', sa.String(), nullable=False),
        sa.Column('side', sa.String(), nullable=False),
        sa.Column('entry', sa.Numeric(20, 8), nullable=False),
        sa.Column('stop_loss', sa.Numeric(20, 8), nullable=False),
        sa.Column('targets', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('status', sa.Enum('OPEN', 'CLOSED', name='usertradestatus'), server_default='OPEN', nullable=False),
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

    # =========================
    # ANALYST STATS
    # =========================
    op.create_table('analyst_stats',
        sa.Column('analyst_profile_id', sa.Integer(), sa.ForeignKey('analyst_profiles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('win_rate', sa.Numeric(5, 2), nullable=True),
        sa.Column('total_pnl', sa.Numeric(10, 4), nullable=True),
        sa.Column('total_trades', sa.Integer(), nullable=True),
        sa.Column('last_updated', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('analyst_stats')
    op.drop_table('user_trades')
    op.drop_table('subscriptions')
    op.drop_table('recommendation_events')
    op.drop_table('published_messages')
    op.drop_table('recommendations')
    op.drop_table('channels')
    op.drop_table('analyst_profiles')
    op.drop_table('users')

    op.execute("DROP TYPE IF EXISTS usertradestatus;")
    op.execute("DROP TYPE IF EXISTS usertype;")
    op.execute("DROP TYPE IF EXISTS exitstrategyenum;")
    op.execute("DROP TYPE IF EXISTS ordertypeenum;")
    op.execute("DROP TYPE IF EXISTS recommendationstatusenum;")