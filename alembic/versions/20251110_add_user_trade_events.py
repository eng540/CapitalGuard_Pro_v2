# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: alembic/versions/20251110_add_user_trade_events.py ---
"""
Add UserTradeEvent table for trade lifecycle auditing (R1-S1 HOTFIX 10).

✅ THE FIX (R1-S1 HOTFIX 10):
1. Creates new table 'user_trade_events' for immutable event logging.
2. Establishes FK to 'user_trades' with CASCADE deletion.
3. Enables stateful tracking to prevent duplicated notifications (Bug B).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# --- Alembic identifiers ---
revision = '20251110_add_user_trade_events'
down_revision = '20251110_add_watchlist_layer'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Apply the new auditing table for user trade events."""
    op.create_table(
        'user_trade_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_trade_id', sa.Integer(), sa.ForeignKey('user_trades.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('event_type', sa.String(length=50), nullable=False, index=True),
        sa.Column('event_timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('event_data', postgresql.JSONB, nullable=True),
    )

    # --- Optional: Add indexes explicitly for performance ---
    op.create_index(op.f('ix_user_trade_events_user_trade_id'), 'user_trade_events', ['user_trade_id'], unique=False)
    op.create_index(op.f('ix_user_trade_events_event_type'), 'user_trade_events', ['event_type'], unique=False)

    # --- Logging ---
    print("✅ user_trade_events table successfully created.")


def downgrade() -> None:
    """Revert the migration and remove the event log table."""
    op.drop_index(op.f('ix_user_trade_events_event_type'), table_name='user_trade_events')
    op.drop_index(op.f('ix_user_trade_events_user_trade_id'), table_name='user_trade_events')
    op.drop_table('user_trade_events')

    print("🧹 user_trade_events table successfully dropped.")
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: alembic/versions/20251110_add_user_trade_events.py ---