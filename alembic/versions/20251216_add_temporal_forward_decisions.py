"""add auditable temporal forward decisions

Revision ID: 20251216_add_temporal_forward_decisions
Revises: 20251215_add_historical_shadow_channels
"""

from alembic import op
import sqlalchemy as sa


revision = "20251216_add_temporal_forward_decisions"
down_revision = "20251215_add_historical_shadow_channels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "temporal_forward_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("receiver_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("receiver_message_id", sa.BigInteger(), nullable=False),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("source_message_id", sa.BigInteger(), nullable=True),
        sa.Column("source_message_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edit_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mode", sa.String(length=40), nullable=False),
        sa.Column("route", sa.String(length=40), nullable=False),
        sa.Column("timeline_relation", sa.String(length=40), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("price_validity_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("age_seconds", sa.Integer(), nullable=True),
        sa.Column("replay_readiness", sa.Numeric(5, 4), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "receiver_chat_id",
            "receiver_message_id",
            name="uq_temporal_forward_receiver_message",
        ),
    )
    for index_name, columns in (
        ("ix_temporal_forward_decisions_receiver_chat_id", ["receiver_chat_id"]),
        ("ix_temporal_forward_decisions_receiver_message_id", ["receiver_message_id"]),
        ("ix_temporal_forward_decisions_source_chat_id", ["source_chat_id"]),
        ("ix_temporal_forward_decisions_source_message_id", ["source_message_id"]),
        ("ix_temporal_forward_decisions_source_time", ["source_time"]),
        ("ix_temporal_forward_decisions_event_time", ["event_time"]),
        ("ix_temporal_forward_decisions_received_time", ["received_time"]),
        ("ix_temporal_forward_decisions_ingested_time", ["ingested_time"]),
        ("ix_temporal_forward_decisions_mode", ["mode"]),
        ("ix_temporal_forward_decisions_route", ["route"]),
        ("ix_temporal_forward_decisions_timeline_relation", ["timeline_relation"]),
    ):
        op.create_index(index_name, "temporal_forward_decisions", columns)


def downgrade() -> None:
    for index_name in (
        "ix_temporal_forward_decisions_timeline_relation",
        "ix_temporal_forward_decisions_route",
        "ix_temporal_forward_decisions_mode",
        "ix_temporal_forward_decisions_ingested_time",
        "ix_temporal_forward_decisions_received_time",
        "ix_temporal_forward_decisions_event_time",
        "ix_temporal_forward_decisions_source_time",
        "ix_temporal_forward_decisions_source_message_id",
        "ix_temporal_forward_decisions_source_chat_id",
        "ix_temporal_forward_decisions_receiver_message_id",
        "ix_temporal_forward_decisions_receiver_chat_id",
    ):
        op.drop_index(index_name, table_name="temporal_forward_decisions")
    op.drop_table("temporal_forward_decisions")
