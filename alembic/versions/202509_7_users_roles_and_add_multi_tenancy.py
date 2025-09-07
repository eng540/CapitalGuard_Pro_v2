# --- START OF FILE: alembic/versions/xxxxxxxx_create_users_roles_and_add_multi_tenancy.py ---
"""Create users, roles and add multi-tenancy foundation

Revision ID: 20250907_multi_tenancy_foundation
Revises: 20250905_add_alert_meta
Create Date: 2025-09-07 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20250907_multi_tenancy_foundation'
# ⚠️ هام: تأكد من أن هذا يطابق revision ID لآخر ملف ترحيل لديك بالفعل
down_revision = '20250905_add_alert_meta'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Step 1: Create new tables for Users and Roles ---
    print("Step 1: Creating users, roles, and user_roles tables...")
    op.create_table('users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('telegram_user_id', sa.BigInteger(), nullable=False),
        sa.Column('user_type', sa.String(length=50), server_default='trader', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('telegram_user_id'),
        sa.Index('ix_users_telegram_user_id', 'telegram_user_id', unique=True)
    )
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
    print("Step 1: Tables created successfully.")

    # --- Step 2: Handle data migration for recommendations table ---
    print("Step 2: Migrating recommendations table for multi-tenancy...")
    
    # Create a default user to assign existing recommendations to.
    # This prevents data loss if recommendations exist without a user.
    bind = op.get_bind()
    users_table = sa.Table('users', sa.MetaData(), sa.Column('id', sa.Integer), sa.Column('telegram_user_id', sa.BigInteger))
    
    # Check if a default user already exists
    result = bind.execute(sa.text("SELECT id FROM users WHERE telegram_user_id = 0")).scalar()
    if result is None:
        print("Action: Creating a default 'Orphan' user.")
        op.execute(users_table.insert().values(telegram_user_id=0, user_type='analyst'))
        
    default_user_id = bind.execute(sa.text("SELECT id FROM users WHERE telegram_user_id = 0")).scalar()
    if default_user_id is None:
        raise RuntimeError("Failed to create or find the default user.")

    # Add the new user_id column to recommendations, making it nullable initially
    op.add_column('recommendations', sa.Column('user_id_new', sa.Integer(), nullable=True))

    # Update existing recommendations to point to the default user
    print(f"Action: Assigning existing recommendations to default user (ID: {default_user_id}).")
    op.execute(f"UPDATE recommendations SET user_id_new = {default_user_id} WHERE user_id IS NULL OR user_id_new IS NULL")
    
    # Now that all rows are populated, alter the column to be NOT NULL
    op.alter_column('recommendations', 'user_id_new', nullable=False)

    # If an old user_id column (non-integer) exists, drop it
    inspector = sa.inspect(bind)
    recs_columns = [c['name'] for c in inspector.get_columns('recommendations')]
    if 'user_id' in recs_columns:
        print("Action: Dropping old 'user_id' column.")
        op.drop_column('recommendations', 'user_id')
    
    # Rename the new column to the final name 'user_id'
    op.alter_column('recommendations', 'user_id_new', new_column_name='user_id')
    
    # Finally, add the foreign key constraint
    op.create_foreign_key(
        "fk_recommendations_user_id",
        'recommendations', 'users',
        ['user_id'], ['id'],
        ondelete="CASCADE"
    )
    print("Step 2: Recommendations table migrated successfully.")


def downgrade() -> None:
    # Downgrade logic should be the reverse of the upgrade
    print("Downgrading multi-tenancy foundation...")
    op.drop_constraint("fk_recommendations_user_id", 'recommendations', type_='foreignkey')
    op.drop_column('recommendations', 'user_id')
    op.drop_table('user_roles')
    op.drop_table('roles')
    op.drop_table('users')
    print("Downgrade complete.")

# --- END OF FILE ---`