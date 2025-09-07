# --- START OF FILE: alembic/versions/xxxxxxxx_foundation_for_multi_tenancy.py ---
"""Foundation for multi-tenancy with users and roles

Revision ID: 20250907_multi_tenancy_final
Revises: 20250905_add_alert_meta
Create Date: 2025-09-07 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20250907_multi_tenancy_final'
down_revision = '20250905_add_alert_meta' # ⚠️ تأكد من أن هذا يطابق آخر revision لديك
branch_labels = None
depends_on = None


def upgrade() -> None:
    print("--- Running Smart Migration for Multi-Tenancy Foundation ---")
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- 1. Synchronize 'users' table (Create if not exists, otherwise alter) ---
    print("Step 1: Synchronizing 'users' table...")
    if not inspector.has_table("users"):
        print("Action: 'users' table not found, creating it.")
        op.create_table('users',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('telegram_user_id', sa.BigInteger(), nullable=False),
            sa.Column('user_type', sa.String(length=50), server_default='trader', nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('telegram_user_id'),
            sa.Index('ix_users_telegram_user_id', 'telegram_user_id', unique=True)
        )
    else:
        print("Check: 'users' table exists. Verifying columns...")
        # هنا يمكنك إضافة منطق `op.add_column` إذا كانت هناك أعمدة مفقودة في المستقبل
        pass
    print("Step 1: 'users' table synchronized.")

    # --- 2. Synchronize 'roles' and 'user_roles' tables ---
    print("Step 2: Synchronizing role-related tables...")
    if not inspector.has_table("roles"):
        op.create_table('roles', sa.Column('id', sa.Integer(), primary_key=True), sa.Column('name', sa.String(64), nullable=False, unique=True))
    if not inspector.has_table("user_roles"):
        op.create_table('user_roles', sa.Column('id', sa.Integer(), primary_key=True), sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False), sa.Column('role_id', sa.Integer(), sa.ForeignKey('roles.id', ondelete='CASCADE'), nullable=False), sa.UniqueConstraint('user_id', 'role_id', name="uq_user_role"))
    print("Step 2: Role tables synchronized.")

    # --- 3. Intelligently migrate 'recommendations.user_id' ---
    print("Step 3: Migrating 'recommendations' table for user foreign key...")
    recs_columns = {c['name']: c for c in inspector.get_columns('recommendations')}
    
    # تحقق مما إذا كان عمود user_id من النوع الصحيح (Integer)
    is_user_id_integer = 'user_id' in recs_columns and isinstance(recs_columns['user_id']['type'], sa.Integer)

    if not is_user_id_integer:
        print("Action: 'recommendations.user_id' needs migration.")
        # الخطوة أ: إنشاء مستخدم افتراضي لاستيعاب التوصيات القديمة
        users_table = sa.Table('users', sa.MetaData(), sa.Column('id', sa.Integer), sa.Column('telegram_user_id', sa.BigInteger))
        result = bind.execute(sa.text("SELECT id FROM users WHERE telegram_user_id = 0")).scalar()
        if result is None:
            op.execute(users_table.insert().values(telegram_user_id=0, user_type='analyst'))
        default_user_id = bind.execute(sa.text("SELECT id FROM users WHERE telegram_user_id = 0")).scalar()

        # الخطوة ب: إضافة عمود جديد ومؤقت
        op.add_column('recommendations', sa.Column('user_id_fk', sa.Integer(), nullable=True))
        
        # الخطوة ج: ملء العمود المؤقت بمعرف المستخدم الافتراضي
        op.execute(f"UPDATE recommendations SET user_id_fk = {default_user_id}")
        
        # الخطوة د: جعل العمود المؤقت إلزاميًا
        op.alter_column('recommendations', 'user_id_fk', nullable=False)
        
        # الخطوة هـ: حذف العمود القديم (إذا كان موجودًا)
        if 'user_id' in recs_columns:
            op.drop_column('recommendations', 'user_id')
            
        # الخطوة و: إعادة تسمية العمود المؤقت إلى الاسم النهائي
        op.alter_column('recommendations', 'user_id_fk', new_column_name='user_id')
        
    # الخطوة الأخيرة: تأكد من وجود المفتاح الخارجي
    fk_exists = any(fk['name'] == 'fk_recommendations_user_id' for fk in inspector.get_foreign_keys('recommendations'))
    if not fk_exists:
        print("Action: Adding foreign key constraint to 'recommendations.user_id'.")
        op.create_foreign_key('fk_recommendations_user_id', 'recommendations', 'users', ['user_id'], ['id'], ondelete='CASCADE')

    print("Step 3: 'recommendations' table migrated successfully.")
    print("--- Smart Migration COMPLETE ---")


def downgrade() -> None:
    # التراجع هنا معقد وقد يسبب فقدان بيانات، لذا سنبقيه بسيطًا
    print("Downgrading is complex and not fully supported for this migration.")
    op.drop_constraint('fk_recommendations_user_id', 'recommendations', type_='foreignkey')
    op.drop_column('recommendations', 'user_id')
    op.drop_table('user_roles')
    op.drop_table('roles')
    op.drop_table('users')
# --- END OF FILE ---