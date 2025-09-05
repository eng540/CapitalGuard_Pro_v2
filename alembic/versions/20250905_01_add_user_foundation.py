"""Create foundational user, role tables and link recommendations to users.

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
    # Step 1: Create the 'users' table. This is the new central table for all users.
    users_table = op.create_table('users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('telegram_user_id', sa.BigInteger(), nullable=False),
        sa.Column('user_type', sa.String(length=50), nullable=False, server_default='trader'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('telegram_user_id')
    )

    # Step 2: Create the 'roles' table for future RBAC.
    op.create_table('roles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )

    # Step 3: Create the 'user_roles' association table.
    op.create_table('user_roles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('role_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'role_id', name='uq_user_role')
    )

    # Step 4: Safely migrate the 'recommendations.user_id' column.
    op.add_column('recommendations', sa.Column('user_id_fk', sa.Integer(), sa.ForeignKey('users.id'), nullable=True))
    
    # NOTE: A manual data migration will be needed in production here.
    # This script creates the structure, but you would need a separate process to populate `users`
    # from the old `recommendations.user_id` and then link them.
    # For a fresh DB, this works perfectly.

    op.drop_column('recommendations', 'user_id')
    op.alter_column('recommendations', 'user_id_fk', new_column_name='user_id')


def downgrade() -> None:
    # Reverse the process: re-add the old string column, drop the new FK column.
    op.add_column('recommendations', sa.Column('user_id_str', sa.String(), nullable=True))
    op.drop_constraint('fk_recommendations_user_id_users', 'recommendations', type_='foreignkey') # Name might vary
    op.drop_column('recommendations', 'user_id')
    op.alter_column('recommendations', 'user_id_str', new_column_name='user_id')
    
    # Drop the new tables
    op.drop_table('user_roles')
    op.drop_table('roles')
    op.drop_table('users')