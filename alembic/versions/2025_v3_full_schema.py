# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: alembic/versions/2025_v3_full_schema.py ---
"""Alembic migration: create parsing_templates and parsing_attempts tables."""
from alembic import op
import sqlalchemy as sa

revision = '2025_v3_full_schema'
down_revision = '20251007_v3_baseline'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        'parsing_templates',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('pattern', sa.Text(), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=True),
        sa.Column('is_public', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('stats', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_parsing_templates_owner', 'parsing_templates', ['owner_id'])

    op.create_table(
        'parsing_attempts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('payload_raw', sa.Text(), nullable=False),
        sa.Column('parsed_json', sa.JSON(), nullable=True),
        sa.Column('success', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('template_id', sa.Integer(), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('corrected_by_user', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('correction_diff', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

def downgrade():
    op.drop_table('parsing_attempts')
    op.drop_index('ix_parsing_templates_owner', table_name='parsing_templates')
    op.drop_table('parsing_templates')
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: alembic/versions/2025_v3_full_schema.py ---
