from alembic import op
import sqlalchemy as sa

revision = "20250825_add_exit_price_closed_at"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS exit_price DOUBLE PRECISION"))
    conn.execute(sa.text("ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS closed_at TIMESTAMP"))

def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE recommendations DROP COLUMN IF EXISTS closed_at"))
    conn.execute(sa.text("ALTER TABLE recommendations DROP COLUMN IF EXISTS exit_price"))