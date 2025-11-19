#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: alembic/versions/20251119_add_status_constraints.py ---
"""Add status check constraints

Revision ID: 20251119_add_status_constraints
Revises: 20251119_fix_enum_statuses
Create Date: 2025-11-19 16:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text

# revision identifiers, used by Alembic.
revision = '20251119_add_status_constraints'
down_revision = '20251119_fix_enum_statuses'
branch_labels = None
depends_on = None

def upgrade() -> None:
    conn = op.get_bind()
    
    # 1. Ensure data is clean (Redundant safety check)
    conn.execute(text("""
        UPDATE recommendations 
        SET status = 'CLOSED' 
        WHERE status::text NOT IN ('PENDING', 'ACTIVE', 'CLOSED');
    """))
    conn.execute(text("""
        UPDATE user_trades 
        SET status = 'CLOSED' 
        WHERE status::text NOT IN ('WATCHLIST', 'PENDING_ACTIVATION', 'ACTIVATED', 'CLOSED');
    """))

    # 2. Add CHECK Constraint for Recommendations
    # Note: We use a raw SQL command because Alembic's op.create_check_constraint 
    # can be tricky with existing Enum types in Postgres.
    op.execute("""
        ALTER TABLE recommendations
        ADD CONSTRAINT valid_recommendation_status
        CHECK (status IN ('PENDING', 'ACTIVE', 'CLOSED'));
    """)
    
    # 3. Add CHECK Constraint for UserTrades
    op.execute("""
        ALTER TABLE user_trades
        ADD CONSTRAINT valid_user_trade_status
        CHECK (status IN ('WATCHLIST', 'PENDING_ACTIVATION', 'ACTIVATED', 'CLOSED'));
    """)
    
    print("✅ Database CHECK constraints applied.")

def downgrade() -> None:
    op.execute("ALTER TABLE recommendations DROP CONSTRAINT IF EXISTS valid_recommendation_status;")
    op.execute("ALTER TABLE user_trades DROP CONSTRAINT IF EXISTS valid_user_trade_status;")
    print("⚠️ Database CHECK constraints removed.")
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: alembic/versions/20251119_add_status_constraints.py ---