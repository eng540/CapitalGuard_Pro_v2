"""add payload to publication delivery outbox

Revision ID: 20251204_add_publication_delivery_payload
Revises: 20251203_add_publication_delivery
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20251204_add_publication_delivery_payload"
down_revision = "20251203_add_publication_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    payload_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.add_column("publication_deliveries", sa.Column("payload_json", payload_type, nullable=True))
    op.drop_constraint("uq_publication_delivery_target_operation", "publication_deliveries", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_publication_delivery_target_operation",
        "publication_deliveries",
        ["recommendation_id", "telegram_channel_id", "operation"],
    )
    op.drop_column("publication_deliveries", "payload_json")
