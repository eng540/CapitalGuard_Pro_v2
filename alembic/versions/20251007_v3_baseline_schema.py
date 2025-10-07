# alembic/versions/20251007_v3_baseline_schema.py (FINAL, SIMPLIFIED & CORRECT)
"""Creates the complete baseline schema for v3.0

Revision ID: 20251007_v3_baseline
Revises: 
Create Date: 2025-10-07 21:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20251007_v3_baseline'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ### Manually crafted, robust, and idempotent schema creation ###
    
    # --- ENUM Types (Idempotent Check using DO-BEGIN block) ---
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'usertype') THEN
                CREATE TYPE usertype AS ENUM ('TRADER', 'ANALYST');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'recommendationstatusenum') THEN
                CREATE TYPE recommendationstatusenum AS ENUM ('PENDING', 'ACTIVE', 'CLOSED');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'ordertypeenum') THEN
                CREATE TYPE ordertypeenum AS ENUM ('MARKET', 'LIMIT', 'STOP_MARKET');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'exitstrategyenum') THEN
                CREATE TYPE exitstrategyenum AS ENUM ('CLOSE_AT_FINAL_TP', 'MANUAL_CLOSE_ONLY');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'usertradestatus') THEN
                CREATE TYPE usertradestatus AS ENUM ('OPEN', 'CLOSED');
            END IF;
        END$$;
    """)

    # --- Tables (No checkfirst needed, Alembic handles this) ---
    
    op.create_table('users',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('telegram_user_id', sa.BigInteger(), nullable=False),
    sa.Column('user_type', sa.Enum('TRADER', 'ANALYST', name='usertype', create_type=False), server_default='TRADER', nullable=False),
    sa.Column('username', sa.String(), nullable=True),
    sa.Column('first_name', sa.String(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('telegram_user_id')
    )
    
    op.create_table('analyst_profiles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('public_name', sa.String(), nullable=True),
    sa.Column('bio', sa.Text(), nullable=True),
    sa.Column('is_public', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id')
    )

    op.create_table('channels',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('analyst_id', sa.Integer(), nullable=False),
    sa.Column('telegram_channel_id', sa.BigInteger(), nullable=False),
    sa.Column('username', sa.String(), nullable=True),
    sa.Column('title', sa.String(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['analyst_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('telegram_channel_id')
    )
    op.create_index(op.f('ix_channels_analyst_id'), 'channels', ['analyst_id'], unique=False)

    op.create_table('recommendations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('analyst_id', sa.Integer(), nullable=False),
    sa.Column('channel_id', sa.Integer(), nullable=True),
    sa.Column('asset', sa.String(), nullable=False),
    sa.Column('side', sa.String(), nullable=False),
    sa.Column('entry', sa.Numeric(precision=20, scale=8), nullable=False),
    sa.Column('stop_loss', sa.Numeric(precision=20, scale=8), nullable=False),
    sa.Column('targets', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'ACTIVE', 'CLOSED', name='recommendationstatusenum', create_type=False), nullable=False),
    sa.Column('order_type', sa.Enum('MARKET', 'LIMIT', 'STOP_MARKET', name='ordertypeenum', create_type=False), nullable=False),
    sa.Column('exit_strategy', sa.Enum('CLOSE_AT_FINAL_TP', 'MANUAL_CLOSE_ONLY', name='exitstrategyenum', create_type=False), nullable=False),
    sa.Column('market', sa.String(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('open_size_percent', sa.Numeric(precision=5, scale=2), server_default=sa.text('100.00'), nullable=False),
    sa.Column('exit_price', sa.Numeric(precision=20, scale=8), nullable=True),
    sa.Column('is_shadow', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['analyst_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_recommendations_analyst_id'), 'recommendations', ['analyst_id'], unique=False)
    op.create_index(op.f('ix_recommendations_asset'), 'recommendations', ['asset'], unique=False)
    op.create_index(op.f('ix_recommendations_status'), 'recommendations', ['status'], unique=False)

    op.create_table('user_trades',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('asset', sa.String(), nullable=False),
    sa.Column('side', sa.String(), nullable=False),
    sa.Column('entry', sa.Numeric(precision=20, scale=8), nullable=False),
    sa.Column('stop_loss', sa.Numeric(precision=20, scale=8), nullable=False),
    sa.Column('targets', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.Enum('OPEN', 'CLOSED', name='usertradestatus', create_type=False), nullable=False),
    sa.Column('close_price', sa.Numeric(precision=20, scale=8), nullable=True),
    sa.Column('pnl_percentage', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('source_recommendation_id', sa.Integer(), nullable=True),
    sa.Column('source_forwarded_text', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['source_recommendation_id'], ['recommendations.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_trades_asset'), 'user_trades', ['asset'], unique=False)
    op.create_index(op.f('ix_user_trades_source_recommendation_id'), 'user_trades', ['source_recommendation_id'], unique=False)
    op.create_index(op.f('ix_user_trades_status'), 'user_trades', ['status'], unique=False)
    op.create_index(op.f('ix_user_trades_user_id'), 'user_trades', ['user_id'], unique=False)

    op.create_table('analyst_stats',
    sa.Column('analyst_profile_id', sa.Integer(), nullable=False),
    sa.Column('win_rate', sa.Numeric(precision=5, scale=2), nullable=True),
    sa.Column('total_pnl', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('total_trades', sa.Integer(), nullable=True),
    sa.Column('last_updated', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['analyst_profile_id'], ['analyst_profiles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('analyst_profile_id')
    )
    op.create_table('published_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('recommendation_id', sa.Integer(), nullable=False),
    sa.Column('telegram_channel_id', sa.BigInteger(), nullable=False),
    sa.Column('telegram_message_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['recommendation_id'], ['recommendations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_published_messages_recommendation_id'), 'published_messages', ['recommendation_id'], unique=False)
    op.create_table('recommendation_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('recommendation_id', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.String(length=50), nullable=False),
    sa.Column('event_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('event_timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['recommendation_id'], ['recommendations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_recommendation_events_event_type'), 'recommendation_events', ['event_type'], unique=False)
    op.create_index(op.f('ix_recommendation_events_recommendation_id'), 'recommendation_events', ['recommendation_id'], unique=False)
    op.create_table('subscriptions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('trader_user_id', sa.Integer(), nullable=False),
    sa.Column('analyst_user_id', sa.Integer(), nullable=False),
    sa.Column('start_date', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('end_date', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['analyst_user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['trader_user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### Manually crafted, robust downgrade ###
    op.drop_index(op.f('ix_user_trades_user_id'), table_name='user_trades')
    op.drop_index(op.f('ix_user_trades_status'), table_name='user_trades')
    op.drop_index(op.f('ix_user_trades_source_recommendation_id'), table_name='user_trades')
    op.drop_index(op.f('ix_user_trades_asset'), table_name='user_trades')
    op.drop_table('user_trades')
    op.drop_table('subscriptions')
    op.drop_index(op.f('ix_recommendation_events_recommendation_id'), table_name='recommendation_events')
    op.drop_index(op.f('ix_recommendation_events_event_type'), table_name='recommendation_events')
    op.drop_table('recommendation_events')
    op.drop_index(op.f('ix_published_messages_recommendation_id'), table_name='published_messages')
    op.drop_table('published_messages')
    op.drop_table('analyst_stats')
    op.drop_index(op.f('ix_recommendations_status'), table_name='recommendations')
    op.drop_index(op.f('ix_recommendations_asset'), table_name='recommendations')
    op.drop_index(op.f('ix_recommendations_analyst_id'), table_name='recommendations')
    op.drop_table('recommendations')
    op.drop_index(op.f('ix_channels_analyst_id'), table_name='channels')
    op.drop_table('channels')
    op.drop_table('analyst_profiles')
    op.drop_table('users')
    
    # Drop ENUM types
    op.execute("DROP TYPE IF EXISTS usertradestatus")
    op.execute("DROP TYPE IF EXISTS exitstrategyenum")
    op.execute("DROP TYPE IF EXISTS ordertypeenum")
    op.execute("DROP TYPE IF EXISTS recommendationstatusenum")
    op.execute("DROP TYPE IF EXISTS usertype")
    # ### end Alembic commands ###