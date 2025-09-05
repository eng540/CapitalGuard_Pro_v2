"""Final and consolidated migration for Phase 0 multi-tenancy foundation.

Revision ID: 20250906_phase0_final
Revises: 20250905_add_alert_meta
Create Date: 2025-09-06 03:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20250906_phase0_final'
down_revision = '20250905_add_alert_meta'
branch_labels = None
depends_on = None

def upgrade() -> None:
    print("--- Running Phase 0 Final Consolidated Migration ---")
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- 1. Synchronize 'users' table ---
    print("Step 1: Synchronizing 'users' table...")
    if not inspector.has_table("users"):
        print("Action: 'users' table not found, creating it from scratch.")
        op.create_table('users',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('telegram_user_id', sa.BigInteger(), nullable=False),
            sa.Column('user_type', sa.String(length=50), nullable=False, server_default='trader'),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('telegram_user_id')
        )
    else:
        print("Check: 'users' table exists. Verifying columns...")
        columns = [c.get('name') for c in inspector.get_columns('users')]
        
        # Verify telegram_user_id
        if 'telegram_user_id' not in columns:
            if 'telegram_id' in columns:
                print("Action: Renaming 'telegram_id' to 'telegram_user_id'.")
                op.alter_column('users', 'telegram_id', new_column_name='telegram_user_id')
            else:
                print("Action: Adding missing column 'telegram_user_id'.")
                op.add_column('users', sa.Column('telegram_user_id', sa.BigInteger(), nullable=False, server_default=sa.text("'0'")))
        
        # Verify user_type
        if 'user_type' not in columns:
            print("Action: Adding missing column 'user_type'.")
            op.add_column('users', sa.Column('user_type', sa.String(length=50), nullable=False, server_default='trader'))
        
        # Verify created_at
        if 'created_at' not in columns:
            print("Action: Adding missing column 'created_at'.")
            op.add_column('users', sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')))
    print("Step 1: 'users' table synchronized.")

    # --- 2. Synchronize 'roles' and 'user_roles' tables ---
    print("Step 2: Synchronizing 'roles' and 'user_roles' tables...")
    if not inspector.has_table("roles"):
        op.create_table('roles', sa.Column('id', sa.Integer(), primary_key=True), sa.Column('name', sa.String(64), nullable=False, unique=True))
    if not inspector.has_table("user_roles"):
        op.create_table('user_roles', sa.Column('id', sa.Integer(), primary_key=True), sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False), sa.Column('role_id', sa.Integer(), sa.ForeignKey('roles.id', ondelete='CASCADE'), nullable=False), sa.UniqueConstraint('user_id', 'role_id'))
    print("Step 2: Role tables synchronized.")

    # --- 3. Synchronize 'recommendations' table user_id foreign key ---
    print("Step 3: Synchronizing 'recommendations' table FK...")
    recs_columns = [c['name'] for c in inspector.get_columns('recommendations')]
    if 'user_id' in recs_columns:
        recs_table_meta = sa.Table('recommendations', sa.MetaData(), autoload_with=bind)
        if not isinstance(recs_table_meta.c.user_id.type, sa.Integer):
            print("Action: Migrating 'recommendations.user_id' from String to Integer FK.")
            op.add_column('recommendations', sa.Column('user_id_fk', sa.Integer(), sa.ForeignKey('users.id'), nullable=True))
            op.drop_column('recommendations', 'user_id')
            op.alter_column('recommendations', 'user_id_fk', new_column_name='user_id')
    else:
         print("Action: Adding missing 'user_id' FK to 'recommendations' table.")
         op.add_column('recommendations', sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True))
    print("Step 3: 'recommendations' table synchronized.")
    print("--- Phase 0 Final Consolidated Migration COMPLETE ---")

def downgrade() -> None:
    # A simple, non-destructive downgrade.
    print("Downgrading Phase 0 migration. Manual check may be required.")
    pass