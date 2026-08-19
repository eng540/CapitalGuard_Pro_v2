"""add per-channel recommendation references

Revision ID: 20251207_add_recommendation_channel_refs
Revises: 20251206_add_channel_catalog
"""
from collections import defaultdict

import sqlalchemy as sa
from alembic import op

revision = "20251207_add_recommendation_channel_refs"
down_revision = "20251206_add_channel_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recommendation_channel_refs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("recommendation_id", sa.Integer(), nullable=False),
        sa.Column("channel_catalog_id", sa.Integer(), nullable=False),
        sa.Column("channel_sequence", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["recommendation_id"], ["recommendations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_catalog_id"], ["channel_catalog.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("recommendation_id", "channel_catalog_id", name="uq_recommendation_channel_ref"),
        sa.UniqueConstraint("channel_catalog_id", "channel_sequence", name="uq_channel_recommendation_sequence"),
    )
    op.create_index("ix_recommendation_channel_refs_recommendation_id", "recommendation_channel_refs", ["recommendation_id"])
    op.create_index("ix_recommendation_channel_refs_channel_catalog_id", "recommendation_channel_refs", ["channel_catalog_id"])

    bind = op.get_bind()
    pairs = set()
    pairs.update(
        (row["recommendation_id"], row["channel_catalog_id"])
        for row in bind.execute(
            sa.text(
                "SELECT r.id AS recommendation_id, c.channel_catalog_id "
                "FROM recommendations r JOIN channels c ON c.id=r.channel_id "
                "WHERE c.channel_catalog_id IS NOT NULL"
            )
        ).mappings()
    )
    pairs.update(
        (row["recommendation_id"], row["channel_catalog_id"])
        for row in bind.execute(
            sa.text(
                "SELECT DISTINCT pd.recommendation_id, cc.id AS channel_catalog_id "
                "FROM publication_deliveries pd "
                "JOIN channel_catalog cc ON cc.telegram_channel_id=pd.telegram_channel_id"
            )
        ).mappings()
    )

    sequences = defaultdict(int)
    for recommendation_id, channel_catalog_id in sorted(pairs, key=lambda item: (item[1], item[0])):
        sequences[channel_catalog_id] += 1
        bind.execute(
            sa.text(
                "INSERT INTO recommendation_channel_refs "
                "(recommendation_id, channel_catalog_id, channel_sequence) "
                "VALUES (:recommendation_id, :channel_catalog_id, :channel_sequence)"
            ),
            {
                "recommendation_id": recommendation_id,
                "channel_catalog_id": channel_catalog_id,
                "channel_sequence": sequences[channel_catalog_id],
            },
        )

    for channel_catalog_id, sequence in sequences.items():
        bind.execute(
            sa.text(
                "INSERT INTO scoped_identity_counters(scope_type, scope_id, next_value) "
                "VALUES ('CHANNEL_RECOMMENDATION', :scope_id, :next_value)"
            ),
            {"scope_id": channel_catalog_id, "next_value": sequence + 1},
        )


def downgrade() -> None:
    op.drop_index("ix_recommendation_channel_refs_channel_catalog_id", table_name="recommendation_channel_refs")
    op.drop_index("ix_recommendation_channel_refs_recommendation_id", table_name="recommendation_channel_refs")
    op.drop_table("recommendation_channel_refs")
