"""add historical signal reconstruction tables

Revision ID: 20251212_add_historical_signal_reconstruction
Revises: 20251211_add_r3_entitlement_ledger
"""
from alembic import op
import sqlalchemy as sa

revision = "20251212_add_historical_signal_reconstruction"
down_revision = "20251211_add_r3_entitlement_ledger"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "historical_import_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_ref", sa.String(length=48), nullable=False, unique=True),
        sa.Column("channel_catalog_id", sa.Integer(), sa.ForeignKey("channel_catalog.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="DRY_RUN", nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("total_records", sa.Integer(), server_default="0", nullable=False),
        sa.Column("accepted_records", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rejected_records", sa.Integer(), server_default="0", nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_historical_import_batches_batch_ref", "historical_import_batches", ["batch_ref"])
    op.create_index("ix_historical_import_batches_channel_catalog_id", "historical_import_batches", ["channel_catalog_id"])
    op.create_index("ix_historical_import_batches_source_kind", "historical_import_batches", ["source_kind"])
    op.create_index("ix_historical_import_batches_requested_by_user_id", "historical_import_batches", ["requested_by_user_id"])
    op.create_index("ix_historical_import_batches_status", "historical_import_batches", ["status"])

    op.create_table(
        "historical_signal_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("historical_import_batches.id", ondelete="SET NULL"), nullable=True),
        sa.Column("channel_catalog_id", sa.Integer(), sa.ForeignKey("channel_catalog.id", ondelete="SET NULL"), nullable=True),
        sa.Column("telegram_channel_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("message_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_uri", sa.String(length=500), nullable=True),
        sa.Column("message_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("dedup_key", sa.String(length=180), nullable=False, unique=True),
        sa.Column("ownership_proof_type", sa.String(length=40), nullable=True),
        sa.Column("ownership_proof_ref", sa.String(length=500), nullable=True),
        sa.Column("evidence_confidence", sa.Numeric(5, 4), server_default="0", nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
    )
    op.create_index("ix_historical_signal_evidence_batch_id", "historical_signal_evidence", ["batch_id"])
    op.create_index("ix_historical_signal_evidence_channel_catalog_id", "historical_signal_evidence", ["channel_catalog_id"])
    op.create_index("ix_historical_signal_evidence_telegram_channel_id", "historical_signal_evidence", ["telegram_channel_id"])
    op.create_index("ix_historical_signal_evidence_telegram_message_id", "historical_signal_evidence", ["telegram_message_id"])
    op.create_index("ix_historical_signal_evidence_source_kind", "historical_signal_evidence", ["source_kind"])
    op.create_index("ix_historical_signal_evidence_message_timestamp", "historical_signal_evidence", ["message_timestamp"])
    op.create_index("ix_historical_signal_evidence_content_hash", "historical_signal_evidence", ["content_hash"])

    op.create_table(
        "historical_signals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_ref", sa.String(length=48), nullable=False, unique=True),
        sa.Column("evidence_id", sa.Integer(), sa.ForeignKey("historical_signal_evidence.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("channel_catalog_id", sa.Integer(), sa.ForeignKey("channel_catalog.id", ondelete="SET NULL"), nullable=True),
        sa.Column("channel_id", sa.Integer(), sa.ForeignKey("channels.id", ondelete="SET NULL"), nullable=True),
        sa.Column("analyst_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("asset", sa.String(length=80), nullable=True),
        sa.Column("side", sa.String(length=16), nullable=True),
        sa.Column("entry", sa.Numeric(20, 8), nullable=True),
        sa.Column("stop_loss", sa.Numeric(20, 8), nullable=True),
        sa.Column("targets", sa.JSON(), nullable=True),
        sa.Column("market", sa.String(length=80), nullable=True),
        sa.Column("decision_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="IMPORTED", nullable=False),
        sa.Column("trust_tier", sa.String(length=32), server_default="UNVERIFIED", nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 4), server_default="0", nullable=False),
        sa.Column("eligible_for_ranking", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_historical_signals_evidence_id", "historical_signals", ["evidence_id"])
    op.create_index("ix_historical_signals_channel_catalog_id", "historical_signals", ["channel_catalog_id"])
    op.create_index("ix_historical_signals_channel_id", "historical_signals", ["channel_id"])
    op.create_index("ix_historical_signals_analyst_id", "historical_signals", ["analyst_id"])
    op.create_index("ix_historical_signals_asset", "historical_signals", ["asset"])
    op.create_index("ix_historical_signals_market", "historical_signals", ["market"])
    op.create_index("ix_historical_signals_decision_timestamp", "historical_signals", ["decision_timestamp"])
    op.create_index("ix_historical_signals_status", "historical_signals", ["status"])
    op.create_index("ix_historical_signals_trust_tier", "historical_signals", ["trust_tier"])
    op.create_index("ix_historical_signals_eligible_for_ranking", "historical_signals", ["eligible_for_ranking"])

    op.create_table(
        "historical_signal_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("signal_id", sa.Integer(), sa.ForeignKey("historical_signals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_evidence_id", sa.Integer(), sa.ForeignKey("historical_signal_evidence.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_source", sa.String(length=80), nullable=True),
        sa.Column("price", sa.Numeric(20, 8), nullable=True),
        sa.Column("replay_status", sa.String(length=24), server_default="UNVERIFIED", nullable=False),
        sa.Column("event_confidence", sa.Numeric(5, 4), server_default="0", nullable=False),
        sa.Column("event_data", sa.JSON(), nullable=True),
        sa.Column("dedup_key", sa.String(length=180), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_historical_signal_events_signal_id", "historical_signal_events", ["signal_id"])
    op.create_index("ix_historical_signal_events_source_evidence_id", "historical_signal_events", ["source_evidence_id"])
    op.create_index("ix_historical_signal_events_event_type", "historical_signal_events", ["event_type"])
    op.create_index("ix_historical_signal_events_event_timestamp", "historical_signal_events", ["event_timestamp"])
    op.create_index("ix_historical_signal_events_market_as_of", "historical_signal_events", ["market_as_of"])
    op.create_index("ix_historical_signal_events_replay_status", "historical_signal_events", ["replay_status"])

    op.create_table(
        "historical_signal_attributions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("signal_id", sa.Integer(), sa.ForeignKey("historical_signals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attribution_kind", sa.String(length=24), nullable=False),
        sa.Column("analyst_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("channel_id", sa.Integer(), sa.ForeignKey("channels.id", ondelete="SET NULL"), nullable=True),
        sa.Column("trader_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("proof_type", sa.String(length=40), nullable=True),
        sa.Column("proof_ref", sa.String(length=500), nullable=True),
        sa.Column("confidence_score", sa.Numeric(5, 4), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="PROPOSED", nullable=False),
        sa.Column("reviewed_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("dedup_key", sa.String(length=180), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_historical_signal_attributions_signal_id", "historical_signal_attributions", ["signal_id"])
    op.create_index("ix_historical_signal_attributions_attribution_kind", "historical_signal_attributions", ["attribution_kind"])
    op.create_index("ix_historical_signal_attributions_analyst_id", "historical_signal_attributions", ["analyst_id"])
    op.create_index("ix_historical_signal_attributions_channel_id", "historical_signal_attributions", ["channel_id"])
    op.create_index("ix_historical_signal_attributions_trader_user_id", "historical_signal_attributions", ["trader_user_id"])
    op.create_index("ix_historical_signal_attributions_status", "historical_signal_attributions", ["status"])
    op.create_index("ix_historical_signal_attributions_reviewed_by_user_id", "historical_signal_attributions", ["reviewed_by_user_id"])


def downgrade():
    for name in [
        "ix_historical_signal_attributions_status",
        "ix_historical_signal_attributions_reviewed_by_user_id",
        "ix_historical_signal_attributions_trader_user_id",
        "ix_historical_signal_attributions_channel_id",
        "ix_historical_signal_attributions_analyst_id",
        "ix_historical_signal_attributions_attribution_kind",
        "ix_historical_signal_attributions_signal_id",
    ]:
        op.drop_index(name, table_name="historical_signal_attributions")
    op.drop_table("historical_signal_attributions")
    for name in [
        "ix_historical_signal_events_replay_status",
        "ix_historical_signal_events_market_as_of",
        "ix_historical_signal_events_event_timestamp",
        "ix_historical_signal_events_event_type",
        "ix_historical_signal_events_source_evidence_id",
        "ix_historical_signal_events_signal_id",
    ]:
        op.drop_index(name, table_name="historical_signal_events")
    op.drop_table("historical_signal_events")
    for name in [
        "ix_historical_signals_eligible_for_ranking",
        "ix_historical_signals_trust_tier",
        "ix_historical_signals_status",
        "ix_historical_signals_decision_timestamp",
        "ix_historical_signals_market",
        "ix_historical_signals_asset",
        "ix_historical_signals_analyst_id",
        "ix_historical_signals_channel_id",
        "ix_historical_signals_channel_catalog_id",
        "ix_historical_signals_evidence_id",
    ]:
        op.drop_index(name, table_name="historical_signals")
    op.drop_table("historical_signals")
    for name in [
        "ix_historical_signal_evidence_content_hash",
        "ix_historical_signal_evidence_batch_id",
        "ix_historical_signal_evidence_message_timestamp",
        "ix_historical_signal_evidence_source_kind",
        "ix_historical_signal_evidence_telegram_message_id",
        "ix_historical_signal_evidence_telegram_channel_id",
        "ix_historical_signal_evidence_channel_catalog_id",
    ]:
        op.drop_index(name, table_name="historical_signal_evidence")
    op.drop_table("historical_signal_evidence")
    for name in [
        "ix_historical_import_batches_status",
        "ix_historical_import_batches_requested_by_user_id",
        "ix_historical_import_batches_source_kind",
        "ix_historical_import_batches_channel_catalog_id",
        "ix_historical_import_batches_batch_ref",
    ]:
        op.drop_index(name, table_name="historical_import_batches")
    op.drop_table("historical_import_batches")
