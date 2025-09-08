#// --- START: alembic/versions/20250909_create_channels_table.py ---
"""Create channels table for analyst broadcasting

Revision ID: 20250909_create_channels_table
Revises: 20250908_make_password_nullable
Create Date: 2025-09-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20250909_create_channels_table'
down_revision = '20250908_make_password_nullable'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- START: Idempotent Upgrade Logic ---
    print("--- Running Idempotent Migration for Channels Table ---")
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- Step 1: Create 'channels' table only if it doesn't exist ---
    if not inspector.has_table("channels"):
        print("Action: 'channels' table not found, creating it.")
        op.create_table('channels',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('telegram_channel_id', sa.BigInteger(), nullable=False),
            sa.Column('username', sa.String(), nullable=False),
            sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('telegram_channel_id'),
            sa.UniqueConstraint('username')
        )
    else:
        print("Check: 'channels' table already exists, skipping creation.")

    # --- Step 2: Create indexes only if they don't exist ---
    indexes = [index['name'] for index in inspector.get_indexes('channels')]
    
    if 'ix_channels_telegram_channel_id' not in indexes:
        print("Action: Creating index 'ix_channels_telegram_channel_id'.")
        op.create_index(op.f('ix_channels_telegram_channel_id'), 'channels', ['telegram_channel_id'], unique=True)
    else:
        print("Check: Index 'ix_channels_telegram_channel_id' already exists.")
        
    if 'ix_channels_user_id' not in indexes:
        print("Action: Creating index 'ix_channels_user_id'.")
        op.create_index(op.f('ix_channels_user_id'), 'channels', ['user_id'], unique=False)
    else:
        print("Check: Index 'ix_channels_user_id' already exists.")

    print("--- Migration complete. ---")
    # --- END: Idempotent Upgrade Logic ---


def downgrade() -> None:
    print("--- Downgrading Channels Table ---")
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("channels"):
        # Drop indexes before dropping the table
        op.drop_index(op.f('ix_channels_user_id'), table_name='channels')
        op.drop_index(op.f('ix_channels_telegram_channel_id'), table_name='channels')
        op.drop_table('channels')
        print("Action: 'channels' table and its indexes dropped.")
    else:
        print("Check: 'channels' table does not exist, skipping downgrade.")
#// --- END: alembic/versions/20250909_create_channels_table.py ---