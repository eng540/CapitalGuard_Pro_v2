# --- START OF SQUASHED, FINAL MIGRATION FILE ---
"""Create initial database schema from all previous migrations

Revision ID: 20250914_create_initial_schema
Revises: 
Create Date: 2025-09-14 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20250914_create_initial_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ### Create all tables from scratch ###

    # 1. Create ENUM types for PostgreSQL first
    op.execute("CREATE TYPE recommendationstatus AS ENUM ('PENDING', 'ACTIVE', 'CLOSED')")
    op.execute("CREATE TYPE ordertype AS ENUM ('MARKET', 'LIMIT', 'STOP_MARKET')")
    op.execute("CREATE TYPE exitstrategy AS ENUM ('CLOSE_AT_FINAL_TP', 'MANUAL_CLOSE_ONLY')")

    # 2. Create 'users' table
    op.create_table('users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('hashed_password', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('telegram_user_id', sa.BigInteger(), nullable=False),
        sa.Column('user_type', sa.String(length=50), server_default='trader', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('first_name', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_index(op.f('ix_users_telegram_user_id'), 'users', ['telegram_user_id'], unique=True)

    # 3. Create 'roles' and 'user_roles' tables
    op.create_table('roles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )
    op.create_table('user_roles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('role_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'role_id', name='uq_user_role')
    )

    # 4. Create 'channels' table
    op.create_table('channels',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('telegram_channel_id', sa.BigInteger(), nullable=False),
        sa.Column('username', sa.String(length=255), nullable=True),
        sa.Column('title', sa.String(length=255), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_channels_telegram_channel_id'), 'channels', ['telegram_channel_id'], unique=True)
    op.create_index(op.f('ix_channels_user_id'), 'channels', ['user_id'], unique=False)
    op.create_index('uq_channels_username_ci', 'channels', [sa.text('lower(username)')], unique=True, postgresql_where=sa.text('username IS NOT NULL'))

    # 5. Create 'recommendations' table with all columns
    op.create_table('recommendations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('asset', sa.String(), nullable=False),
        sa.Column('side', sa.String(), nullable=False),
        sa.Column('entry', sa.Float(), nullable=False),
        sa.Column('stop_loss', sa.Float(), nullable=False),
        sa.Column('targets', sa.JSON(), nullable=False),
        sa.Column('order_type', sa.Enum('MARKET', 'LIMIT', 'STOP_MARKET', name='ordertype'), nullable=False, server_default='LIMIT'),
        sa.Column('status', sa.Enum('PENDING', 'ACTIVE', 'CLOSED', name='recommendationstatus'), nullable=False, server_default='PENDING'),
        sa.Column('channel_id', sa.BigInteger(), nullable=True),
        sa.Column('message_id', sa.BigInteger(), nullable=True),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('market', sa.String(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('exit_price', sa.Float(), nullable=True),
        sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('alert_meta', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('highest_price_reached', sa.Float(), nullable=True),
        sa.Column('lowest_price_reached', sa.Float(), nullable=True),
        sa.Column('exit_strategy', sa.Enum('CLOSE_AT_FINAL_TP', 'MANUAL_CLOSE_ONLY', name='exitstrategy'), server_default='CLOSE_AT_FINAL_TP', nullable=False),
        sa.Column('profit_stop_price', sa.Float(), nullable=True),
        sa.Column('open_size_percent', sa.Float(), server_default=sa.text('100.0'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_recommendations_asset'), 'recommendations', ['asset'], unique=False)
    op.create_index(op.f('ix_recommendations_channel_id'), 'recommendations', ['channel_id'], unique=False)
    op.create_index(op.f('ix_recommendations_id'), 'recommendations', ['id'], unique=False)
    op.create_index(op.f('ix_recommendations_status'), 'recommendations', ['status'], unique=False)
    op.create_index(op.f('ix_recommendations_user_id'), 'recommendations', ['user_id'], unique=False)

    # 6. Create 'published_messages' table
    op.create_table('published_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('recommendation_id', sa.Integer(), nullable=False),
        sa.Column('telegram_channel_id', sa.BigInteger(), nullable=False),
        sa.Column('telegram_message_id', sa.BigInteger(), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['recommendation_id'], ['recommendations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_published_messages_recommendation_id'), 'published_messages', ['recommendation_id'], unique=False)

    # 7. Create 'recommendation_events' table
    op.create_table('recommendation_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('recommendation_id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('event_timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('event_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(['recommendation_id'], ['recommendations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_recommendation_events_event_type'), 'recommendation_events', ['event_type'], unique=False)
    op.create_index(op.f('ix_recommendation_events_recommendation_id'), 'recommendation_events', ['recommendation_id'], unique=False)

    # 8. Create trigger function for updated_at
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_recommendations_set_updated_at
        BEFORE UPDATE ON recommendations
        FOR EACH ROW
        EXECUTE FUNCTION set_updated_at();
        """
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.execute("DROP TRIGGER IF EXISTS trg_recommendations_set_updated_at ON recommendations;")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at();")
    
    op.drop_index(op.f('ix_recommendation_events_recommendation_id'), table_name='recommendation_events')
    op.drop_index(op.f('ix_recommendation_events_event_type'), table_name='recommendation_events')
    op.drop_table('recommendation_events')
    
    op.drop_index(op.f('ix_published_messages_recommendation_id'), table_name='published_messages')
    op.drop_table('published_messages')
    
    op.drop_index(op.f('ix_recommendations_user_id'), table_name='recommendations')
    op.drop_index(op.f('ix_recommendations_status'), table_name='recommendations')
    op.drop_index(op.f('ix_recommendations_id'), table_name='recommendations')
    op.drop_index(op.f('ix_recommendations_channel_id'), table_name='recommendations')
    op.drop_index(op.f('ix_recommendations_asset'), table_name='recommendations')
    op.drop_table('recommendations')
    
    op.drop_index('uq_channels_username_ci', table_name='channels')
    op.drop_index(op.f('ix_channels_user_id'), table_name='channels')
    op.drop_index(op.f('ix_channels_telegram_channel_id'), table_name='channels')
    op.drop_table('channels')
    
    op.drop_table('user_roles')
    op.drop_table('roles')
    
    op.drop_index(op.f('ix_users_telegram_user_id'), table_name='users')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    
    op.execute("DROP TYPE IF EXISTS exitstrategy;")
    op.execute("DROP TYPE IF EXISTS ordertype;")
    op.execute("DROP TYPE IF EXISTS recommendationstatus;")
    # ### end Alembic commands ###
# --- END OF SQUASHED, FINAL MIGRATION FILE ---