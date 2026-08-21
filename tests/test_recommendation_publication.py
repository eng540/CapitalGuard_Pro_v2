from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from capitalguard.application.services.creation_service import CreationService
from capitalguard.application.services.publication_outbox_service import PublicationOutboxService
from capitalguard.domain.entities import UserType as UserTypeEntity
from capitalguard.infrastructure.db.models import (
    Channel,
    OrderTypeEnum,
    Recommendation,
    RecommendationStatusEnum,
    PublishedMessage,
    PublicationDelivery,
    PublicationDeliveryStatus,
)
from capitalguard.infrastructure.db.repository import RecommendationRepository, UserRepository


@pytest.mark.asyncio
async def test_publication_retry_skips_already_published_channel(db_session):
    analyst = UserRepository(db_session).find_or_create(
        telegram_id=99101,
        first_name="Publisher",
        user_type=UserTypeEntity.ANALYST,
        is_active=True,
    )
    channel = Channel(
        analyst_id=analyst.id,
        telegram_channel_id=-10099101,
        username="test_channel",
        title="Test Channel",
        is_active=True,
    )
    db_session.add(channel)
    recommendation = Recommendation(
        analyst_id=analyst.id,
        asset="BTCUSDT",
        side="LONG",
        entry=Decimal("100"),
        stop_loss=Decimal("95"),
        targets=[{"price": "105", "close_percent": 100.0}],
        status=RecommendationStatusEnum.PENDING,
        order_type=OrderTypeEnum.LIMIT,
        market="Futures",
        is_shadow=False,
    )
    db_session.add(recommendation)
    db_session.flush()

    notifier = MagicMock()
    notifier.bot_username = "TestBot"
    notifier.post_to_channel = AsyncMock(return_value=(-10099101, 777))
    service = CreationService(
        RecommendationRepository(),
        notifier,
        MagicMock(),
        MagicMock(),
    )
    entity = service.repo._to_entity(recommendation)

    _, first_report = await service._publish_recommendation(
        db_session, entity, analyst.id, {channel.telegram_channel_id}
    )
    db_session.flush()
    _, second_report = await service._publish_recommendation(
        db_session, entity, analyst.id, {channel.telegram_channel_id}
    )

    assert first_report["success"] == [{"channel_id": channel.telegram_channel_id}]
    assert len(second_report["skipped"]) == 1
    assert second_report["success"] == []
    assert notifier.post_to_channel.await_count == 1
    assert db_session.query(PublishedMessage).filter_by(recommendation_id=recommendation.id).count() == 1


@pytest.mark.asyncio
async def test_creation_queues_outbox_before_any_telegram_delivery(db_session):
    analyst = UserRepository(db_session).find_or_create(
        telegram_id=99102, first_name="Queued", user_type=UserTypeEntity.ANALYST, is_active=True
    )
    channel = Channel(analyst_id=analyst.id, telegram_channel_id=-10099102, title="Queued", is_active=True)
    db_session.add(channel)
    notifier = MagicMock()
    outbox = PublicationOutboxService(RecommendationRepository(), notifier)
    service = CreationService(RecommendationRepository(), notifier, MagicMock(), MagicMock(), outbox_service=outbox)
    rec, receipt = await service.create_and_publish_recommendation_async(
        str(analyst.telegram_user_id), db_session, asset="BTCUSDT", side="LONG", market="Futures",
        order_type="LIMIT", entry="100", stop_loss="95", targets=[{"price": "105", "close_percent": 100}],
        target_channel_ids={channel.telegram_channel_id},
    )
    delivery = db_session.query(PublicationDelivery).filter_by(recommendation_id=rec.id).one()
    assert receipt["queued"] is True
    assert delivery.status == PublicationDeliveryStatus.PENDING.value
    assert not notifier.post_to_channel.called
