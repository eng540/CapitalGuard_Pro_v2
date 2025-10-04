"""Initial schema for v3 (clean reset)

Revision ID: 20251004_initial_v3_schema
Revises: 
Create Date: 2025-10-04 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20251004_initial_v3_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================
    # ENUM Types
    # =========================
    op.execute("CREATE TYPE recommendationstatus AS ENUM ('PENDING', 'ACTIVE', 'CLOSED');")
    op.execute("CREATE TYPE ordertype AS ENUM ('MARKET', 'LIMIT', 'STOP_MARKET');")
    op.execute("CREATE TYPE exitstrategy AS ENUM ('CLOSE_AT_FINAL_TP', 'MANUAL_CLOSE_ONLY');")
    op.execute("CREATE TYPE usertype AS ENUM ('TRADER', 'ANALYST');")
    op.execute("CREATE TYPE usertradestatus AS ENUM ('OPEN', 'CLOSED');")

    # =========================
    # Tables
    # =========================
    op.create_table('users',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('telegram_user_id', sa.BigInteger, nullable=False, unique=True, index=True),
        sa.Column('user_type', sa.Enum('TRADER', 'ANALYST', name='usertype'), nullable=False, server_default='TRADER'),
        sa.Column('username', sa.String, nullable=True),
        sa.Column('first_name', sa.String, nullable=True),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )

    op.create_table('analyst_profiles',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column('public_name', sa.String, nullable=True),
        sa.Column('bio', sa.Text, nullable=True),
        sa.Column('is_public', sa.Boolean, nullable=True),
    )

    op.create_table('channels',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('analyst_id', sa.Integer, sa.ForeignKey('users.id', ondelete="CASCADE"), nullable=False),
        sa.Column('telegram_channel_id', sa.BigInteger, nullable=False, unique=True),
        sa.Column('username', sa.String(255), nullable=True),
        sa.Column('title', sa.String(255), nullable=True),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes', sa.Text, nullable=True),
    )
    op.create_index("ix_channels_analyst_id", "channels", ["analyst_id"])
    op.execute("""
        CREATE UNIQUE INDEX uq_channels_username_ci
        ON channels (lower(username))
        WHERE username IS NOT NULL;
    """)

    op.create_table('recommendations',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('analyst_id', sa.Integer, sa.ForeignKey('users.id', ondelete="CASCADE"), nullable=False),
        sa.Column('asset', sa.String, nullable=False),
        sa.Column('side', sa.String, nullable=False),
        sa.Column('entry', sa.Numeric(20, 8), nullable=False),
        sa.Column('stop_loss', sa.Numeric(20, 8), nullable=False),
        sa.Column('targets', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('order_type', sa.Enum('MARKET', 'LIMIT', 'STOP_MARKET', name='ordertype'), nullable=False, server_default='LIMIT'),
        sa.Column('status', sa.Enum('PENDING', 'ACTIVE', 'CLOSED', name='recommendationstatus'), nullable=False, server_default='PENDING'),
        sa.Column('channel_id', sa.BigInteger, nullable=True),
        sa.Column('message_id', sa.BigInteger, nullable=True),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('market', sa.String, nullable=True),
        sa.Column('notes', sa.Text, nullable=True),
        sa.Column('exit_price', sa.Numeric(20, 8), nullable=True),
        sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.Column('alert_meta', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('highest_price_reached', sa.Numeric(20, 8), nullable=True),
        sa.Column('lowest_price_reached', sa.Numeric(20, 8), nullable=True),
        sa.Column('exit_strategy', sa.Enum('CLOSE_AT_FINAL_TP', 'MANUAL_CLOSE_ONLY', name='exitstrategy'), nullable=False, server_default='CLOSE_AT_FINAL_TP'),
        sa.Column('profit_stop_price', sa.Numeric(20, 8), nullable=True),
        sa.Column('open_size_percent', sa.Numeric(5, 2), nullable=False, server_default='100.00'),
    )
    op.create_index("ix_recommendations_asset", "recommendations", ["asset"])
    op.create_index("ix_recommendations_analyst_id", "recommendations", ["analyst_id"])
    op.create_index("ix_recommendations_status", "recommendations", ["status"])
    op.create_index("ix_recommendations_published_at", "recommendations", ["published_at"])

    op.create_table('published_messages',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('recommendation_id', sa.Integer, sa.ForeignKey('recommendations.id', ondelete="CASCADE"), nullable=False),
        sa.Column('telegram_channel_id', sa.BigInteger, nullable=False),
        sa.Column('telegram_message_id', sa.BigInteger, nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table('recommendation_events',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('recommendation_id', sa.Integer, sa.ForeignKey('recommendations.id', ondelete="CASCADE"), nullable=False),
        sa.Column('event_type', sa.String(50), nullable=False),
        sa.Column('event_timestamp', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('event_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.create_table('subscriptions',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('trader_user_id', sa.Integer, sa.ForeignKey('users.id', ondelete="CASCADE"), nullable=False),
        sa.Column('analyst_user_id', sa.Integer, sa.ForeignKey('users.id', ondelete="CASCADE"), nullable=False),
        sa.Column('start_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('end_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean, nullable=True),
    )

    op.create_table('user_trades',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete="CASCADE"), nullable=False),
        sa.Column('asset', sa.String, nullable=False),
        sa.Column('side', sa.String, nullable=False),
        sa.Column('entry', sa.Numeric(20, 8), nullable=False),
        sa.Column('stop_loss', sa.Numeric(20, 8), nullable=False),
        sa.Column('targets', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('status', sa.Enum('OPEN', 'CLOSED', name='usertradestatus'), nullable=False),
        sa.Column('close_price', sa.Numeric(20, 8), nullable=True),
        sa.Column('pnl_percentage', sa.Numeric(10, 4), nullable=True),
        sa.Column('source_recommendation_id', sa.Integer, sa.ForeignKey('recommendations.id', ondelete="SET NULL"), nullable=True),
        sa.Column('source_forwarded_text', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_user_trades_user_id", "user_trades", ["user_id"])
    op.create_index("ix_user_trades_source_recommendation_id", "user_trades", ["source_recommendation_id"])


def downgrade() -> None:
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
    op.execute("DROP TYPE IF EXISTS exitstrategy;")
    op.execute("DROP TYPE IF EXISTS ordertype;")
    op.execute("DROP TYPE IF EXISTS recommendationstatus;")