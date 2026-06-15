#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: alembic/versions/20251119_fix_enum_statuses.py ---
"""Fix invalid enum statuses in database

Revision ID: 20251119_fix_enum_statuses
Revises: 20251110_add_user_trade_events_safe
Create Date: 2025-11-19 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text

# revision identifiers, used by Alembic.
revision = '20251119_fix_enum_statuses'
down_revision = '20251110_add_user_trade_events_safe'
branch_labels = None
depends_on = None

def upgrade() -> None:
    conn = op.get_bind()
    
    # 1. Clean up Recommendations table
    # Any status that is NOT (PENDING, ACTIVE, CLOSED) should be marked as CLOSED.
    # This handles legacy 'STOPPED' or 'TAKE_PROFIT' if they were inserted via raw SQL.
    conn.execute(text("""
        UPDATE recommendations 
        SET status = 'CLOSED' 
        WHERE status::text NOT IN ('PENDING', 'ACTIVE', 'CLOSED');
    """))
    
    # 2. Clean up UserTrades table
    # Ensure only valid UserTradeStatusEnum values exist.
    conn.execute(text("""
        UPDATE user_trades 
        SET status = 'CLOSED' 
        WHERE status::text NOT IN ('WATCHLIST', 'PENDING_ACTIVATION', 'ACTIVATED', 'CLOSED');
    """))
    
    print("✅ Database statuses sanitized.")

def downgrade() -> None:
    # No downgrade needed as this is a data sanitization migration.
    pass
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: alembic/versions/20251119_fix_enum_statuses.py ---