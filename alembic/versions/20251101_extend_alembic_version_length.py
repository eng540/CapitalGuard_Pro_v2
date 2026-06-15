# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: migrations/20251101_extend_alembic_version_length.py ---
"""Extend alembic_version.version_num from VARCHAR(32) to VARCHAR(64)"""

from alembic import op
import sqlalchemy as sa

revision = '20251101_extend_alembic_version_length'
down_revision = '20251028_add_parsing_infra_fixed'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        'alembic_version',
        'version_num',
        type_=sa.String(length=64),
        existing_type=sa.String(length=32),
        existing_nullable=False
    )
    print("✅ Extended alembic_version.version_num to VARCHAR(64)")


def downgrade() -> None:
    op.alter_column(
        'alembic_version',
        'version_num',
        type_=sa.String(length=32),
        existing_type=sa.String(length=64),
        existing_nullable=False
    )
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: migrations/20251101_extend_alembic_version_length.py ---