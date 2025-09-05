"""Create foundational user, role tables and link recommendations to users (Idempotent)

Revision ID: 20250905_01_add_user_foundation
Revises: 20250905_add_alert_meta
Create Date: 2025-09-05 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20250905_01_add_user_foundation'
down_revision = '20250905_add_alert_meta'
branch_labels = None
depends_on = None

def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- Idempotent Table Creation ---

    # Step 1: Create 'users' table only if it doesn't already exist.
    if not inspector.has_table("users"):
        op.create_table('users',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('telegram_user_id', sa.BigInteger(), nullable=False),
            sa.Column('user_type', sa.String(length=50), nullable=False, server_default='trader'),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('telegram_user_id')
        )

    # Step 2: Create 'roles' table only if it doesn't already exist.
    if not inspector.has_table("roles"):
        op.create_table('roles',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=64), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('name')
        )

    # Step 3: Create 'user_roles' table only if it doesn't already exist.
    if not inspector.has_table("user_roles"):
        op.create_table('user_roles',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('role_id', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('user_id', 'role_id', name='uq_user_role')
        )

    # --- Idempotent Column Migration for 'recommendations' table ---
    
    recs_columns = [c['name'] for c in inspector.get_columns('recommendations')]
    recs_table_metadata = sa.Table('recommendations', sa.MetaData(), autoload_with=bind)

    # Check if the final state (an integer user_id column) is already achieved.
    is_migration_complete = 'user_id' in recs_columns and isinstance(recs_table_metadata.c.user_id.type, sa.Integer)

    if not is_migration_complete:
        # Add the temporary foreign key column if it doesn't exist
        if 'user_id_fk' not in recs_columns:
            op.add_column('recommendations', sa.Column('user_id_fk', sa.Integer(), sa.ForeignKey('users.id'), nullable=True))
        
        # Drop the old string column if it still exists
        if 'user_id' in recs_columns:
            op.drop_column('recommendations', 'user_id')
        
        # Rename the temporary column to its final name
        op.alter_column('recommendations', 'user_id_fk', new_column_name='user_id')


def downgrade() -> None:
    # Downgrade logic remains largely the same, as it's less likely to be run on a broken state.
    op.add_column('recommendations', sa.Column('user_id_str', sa.String(), nullable=True))
    try:
        op.drop_constraint('recommendations_user_id_fkey', 'recommendations', type_='foreignkey')
    except Exception:
        # In case the FK has a different auto-generated name
        pass
    op.drop_column('recommendations', 'user_id')
    op.alter_column('recommendations', 'user_id_str', new_column_name='user_id')
    
    op.drop_table('user_roles')
    op.drop_table('roles')
    op.drop_table('users')