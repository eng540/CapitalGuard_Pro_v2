"""repair legacy tracked user trade identity

Revision ID: 20251209_repair_tracked_trade_identity
Revises: 20251208_add_user_trade_open_size
"""
from alembic import op
import sqlalchemy as sa

revision = "20251209_repair_tracked_trade_identity"
down_revision = "20251208_add_user_trade_open_size"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE user_trades SET source_type = 'TRACKED_RECOMMENDATION' "
        "WHERE source_recommendation_id IS NOT NULL "
        "AND source_type <> 'TRACKED_RECOMMENDATION'"
    ))
    bind.execute(sa.text(
        "UPDATE user_trades SET watched_channel_id = ("
        "SELECT wc.id FROM watched_channels wc "
        "JOIN channels c ON c.telegram_channel_id = wc.telegram_channel_id "
        "JOIN recommendations r ON r.channel_id = c.id "
        "WHERE r.id = user_trades.source_recommendation_id "
        "AND wc.user_id = user_trades.user_id LIMIT 1) "
        "WHERE user_trades.source_recommendation_id IS NOT NULL "
        "AND user_trades.watched_channel_id IS NULL"
    ))


def downgrade() -> None:
    # Do not destroy provenance during downgrade; identity repair is intentionally additive.
    pass
