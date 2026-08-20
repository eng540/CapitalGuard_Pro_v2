from datetime import datetime, timezone

from sqlalchemy import select

from capitalguard.application.services.frictionless_ingestion_service import FrictionlessIngestionService
from capitalguard.application.services.historical_forwarding_service import ForwardedMessageInput
from capitalguard.domain.entities import UserType
from capitalguard.infrastructure.db.models import (
    ChannelCatalog,
    HistoricalShadowChannel,
    PublicationDelivery,
    Recommendation,
    UserTrade,
)
from capitalguard.infrastructure.db.repository import UserRepository


def _message(receiver_id: int, source_id: str = "-100777001", message_id: int = 44):
    return ForwardedMessageInput(
        receiver_chat_id=receiver_id,
        receiver_message_id=receiver_id + message_id,
        forwarding_user_id=1,
        source_chat_id=source_id,
        source_message_id=message_id,
        source_origin_type="CHANNEL",
        source_message_timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        raw_text="#BTCUSDT LONG Entry 70000 SL 69000 TP1 71000",
    )


def test_frictionless_discovers_shadow_and_reuses_auto_batch(db_session):
    reviewer = UserRepository(db_session).find_or_create(
        telegram_id=940001,
        user_type=UserType.TRADER,
        first_name="Direct Reviewer",
    )
    service = FrictionlessIngestionService()

    source = service.discover_source(
        db_session,
        telegram_channel_id="-100777001",
        title="Unclaimed Signals",
        username="unclaimed_signals",
        discovered_by_user_id=reviewer.id,
    )
    assert source.claim_status == "UNCLAIMED"
    assert source.canonical_catalog_id is None
    assert source.shadow_channel_id is not None

    batch = service.start_or_reuse_auto_batch(
        db_session,
        source=source,
        requested_by_user_id=reviewer.id,
    )
    receipt = service.stage_direct_message(
        db_session,
        batch_id=batch.id,
        message=_message(reviewer.telegram_user_id),
    )
    same_source = service.discover_source(
        db_session,
        telegram_channel_id=-100777001,
        title="Unclaimed Signals Renamed",
        username="unclaimed_signals",
        discovered_by_user_id=reviewer.id,
    )
    reused = service.start_or_reuse_auto_batch(
        db_session,
        source=same_source,
        requested_by_user_id=reviewer.id,
        existing_batch_id=batch.id,
    )

    assert receipt.validation_status == "STAGED"
    assert reused.id == batch.id
    assert db_session.scalar(select(HistoricalShadowChannel.sample_count).where(HistoricalShadowChannel.id == source.shadow_channel_id)) == 2
    assert db_session.query(Recommendation).count() == 0
    assert db_session.query(UserTrade).count() == 0
    assert db_session.query(PublicationDelivery).count() == 0


def test_frictionless_attaches_existing_canonical_channel_without_creating_shadow(db_session):
    reviewer = UserRepository(db_session).find_or_create(
        telegram_id=940002,
        user_type=UserType.TRADER,
        first_name="Canonical Reviewer",
    )
    catalog = ChannelCatalog(
        telegram_channel_id=-100777002,
        channel_code="CH-DIRECT-02",
        public_ref="CH-DIRECT-02",
        title="Canonical Signals",
    )
    db_session.add(catalog)
    db_session.flush()
    service = FrictionlessIngestionService()

    source = service.discover_source(
        db_session,
        telegram_channel_id=-100777002,
        title="Canonical Signals",
        username="canonical_signals",
        discovered_by_user_id=reviewer.id,
    )
    batch = service.start_or_reuse_auto_batch(
        db_session,
        source=source,
        requested_by_user_id=reviewer.id,
    )
    receipt = service.stage_direct_message(
        db_session,
        batch_id=batch.id,
        message=_message(reviewer.telegram_user_id, source_id="-100777002", message_id=45),
    )

    assert source.canonical_catalog_id == catalog.id
    assert source.shadow_channel_id is None
    assert receipt.validation_status == "STAGED"
    assert db_session.query(HistoricalShadowChannel).filter_by(telegram_channel_id=-100777002).count() == 0
