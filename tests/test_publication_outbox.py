from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from capitalguard.application.services.publication_outbox_service import PublicationOutboxService
from capitalguard.infrastructure.db.models import (
    OrderTypeEnum,
    PublicationDelivery,
    PublicationDeliveryStatus,
    PublishedMessage,
    Recommendation,
    RecommendationStatusEnum,
)
from capitalguard.infrastructure.db.repository import RecommendationRepository


@contextmanager
def _session_scope_for(session):
    yield session


def _recommendation(session):
    recommendation = Recommendation(
        analyst_id=1,
        asset="ETHUSDT",
        side="SHORT",
        entry=Decimal("2000"),
        stop_loss=Decimal("2050"),
        targets=[{"price": "1950", "close_percent": 100.0}],
        status=RecommendationStatusEnum.PENDING,
        order_type=OrderTypeEnum.LIMIT,
        market="Futures",
        is_shadow=False,
    )
    session.add(recommendation)
    session.flush()
    return recommendation


def test_enqueue_create_delivery_is_idempotent(db_session):
    recommendation = _recommendation(db_session)
    service = PublicationOutboxService(RecommendationRepository(), MagicMock())

    first = service.enqueue_create_deliveries(db_session, recommendation.id, [-1001, -1002])
    second = service.enqueue_create_deliveries(db_session, recommendation.id, [-1001, -1002])

    assert len(first) == 2
    assert len(second) == 2
    assert db_session.query(PublicationDelivery).filter_by(recommendation_id=recommendation.id).count() == 2
    assert db_session.query(PublicationDelivery).filter_by(
        recommendation_id=recommendation.id,
        status=PublicationDeliveryStatus.PENDING.value,
    ).count() == 2


@pytest.mark.asyncio
async def test_outbox_marks_delivery_sent_and_persists_message(db_session, monkeypatch):
    recommendation = _recommendation(db_session)
    repo = RecommendationRepository()
    notifier = MagicMock()
    notifier.bot_username = "TestBot"
    notifier.post_to_channel = AsyncMock(return_value=(-1001, 501))
    service = PublicationOutboxService(repo, notifier)
    delivery = service.enqueue_create_deliveries(db_session, recommendation.id, [-1001])[0]
    db_session.flush()
    monkeypatch.setattr(
        "capitalguard.application.services.publication_outbox_service.session_scope",
        lambda: _session_scope_for(db_session),
    )

    result = await service._deliver_one(delivery.id, repo._to_entity(recommendation))

    assert result["bucket"] == "success"
    saved = db_session.query(PublicationDelivery).filter_by(id=delivery.id).one()
    assert saved.status == PublicationDeliveryStatus.SENT.value
    assert saved.telegram_message_id == 501
    assert db_session.query(PublishedMessage).filter_by(
        recommendation_id=recommendation.id,
        telegram_channel_id=-1001,
    ).count() == 1


@pytest.mark.asyncio
async def test_outbox_records_terminal_failure(db_session, monkeypatch):
    recommendation = _recommendation(db_session)
    repo = RecommendationRepository()
    notifier = MagicMock()
    notifier.bot_username = "TestBot"
    notifier.post_to_channel = AsyncMock(side_effect=RuntimeError("telegram unavailable"))
    service = PublicationOutboxService(repo, notifier, max_attempts=1)
    delivery = service.enqueue_create_deliveries(db_session, recommendation.id, [-1002])[0]
    db_session.flush()
    monkeypatch.setattr(
        "capitalguard.application.services.publication_outbox_service.session_scope",
        lambda: _session_scope_for(db_session),
    )

    result = await service._deliver_one(delivery.id, repo._to_entity(recommendation))

    assert result["bucket"] == "failed"
    saved = db_session.query(PublicationDelivery).filter_by(id=delivery.id).one()
    assert saved.status == PublicationDeliveryStatus.FAILED.value
    assert "telegram unavailable" in saved.last_error


@pytest.mark.asyncio
async def test_outbox_recovers_after_transient_failure_without_duplicate_message(db_session, monkeypatch):
    recommendation = _recommendation(db_session)
    repo = RecommendationRepository()
    notifier = MagicMock()
    notifier.bot_username = "TestBot"
    notifier.post_to_channel = AsyncMock(side_effect=[RuntimeError("temporary outage"), (-1003, 701)])
    service = PublicationOutboxService(repo, notifier, max_attempts=2)
    delivery = service.enqueue_create_deliveries(db_session, recommendation.id, [-1003])[0]
    db_session.flush()
    monkeypatch.setattr(
        "capitalguard.application.services.publication_outbox_service.session_scope",
        lambda: _session_scope_for(db_session),
    )

    first = await service._deliver_one(delivery.id, repo._to_entity(recommendation))
    second = await service._deliver_one(delivery.id, repo._to_entity(recommendation))

    assert first["bucket"] == "retry"
    assert second["bucket"] == "success"
    saved = db_session.query(PublicationDelivery).filter_by(id=delivery.id).one()
    assert saved.status == PublicationDeliveryStatus.SENT.value
    assert saved.telegram_message_id == 701
    assert db_session.query(PublishedMessage).filter_by(
        recommendation_id=recommendation.id,
        telegram_channel_id=-1003,
    ).count() == 1


@pytest.mark.asyncio
async def test_outbox_update_delivery_edits_existing_message(db_session, monkeypatch):
    recommendation = _recommendation(db_session)
    repo = RecommendationRepository()
    notifier = MagicMock()
    notifier.bot_username = "TestBot"
    notifier.edit_recommendation_card_by_ids = AsyncMock()
    service = PublicationOutboxService(repo, notifier)
    db_session.add(PublishedMessage(
        recommendation_id=recommendation.id,
        telegram_channel_id=-1001,
        telegram_message_id=501,
    ))
    delivery = service.enqueue_operation(
        db_session,
        recommendation.id,
        [-1001],
        "UPDATE",
        "event:42",
    )[0]
    db_session.flush()
    monkeypatch.setattr(
        "capitalguard.application.services.publication_outbox_service.session_scope",
        lambda: _session_scope_for(db_session),
    )

    result = await service._deliver_one(delivery.id, repo._to_entity(recommendation))

    assert result["bucket"] == "success"
    notifier.edit_recommendation_card_by_ids.assert_awaited_once()
    assert db_session.query(PublicationDelivery).filter_by(id=delivery.id).one().status == PublicationDeliveryStatus.SENT.value


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["REPLY", "CLOSE"])
async def test_outbox_reply_and_close_delivery_use_payload(db_session, monkeypatch, operation):
    recommendation = _recommendation(db_session)
    repo = RecommendationRepository()
    notifier = MagicMock()
    notifier.post_notification_reply = AsyncMock()
    service = PublicationOutboxService(repo, notifier)
    db_session.add(PublishedMessage(
        recommendation_id=recommendation.id,
        telegram_channel_id=-1001,
        telegram_message_id=501,
    ))
    delivery = service.enqueue_operation(
        db_session,
        recommendation.id,
        [-1001],
        operation,
        f"event:{operation.lower()}",
        payload={"text": f"{operation} update"},
    )[0]
    db_session.flush()
    monkeypatch.setattr(
        "capitalguard.application.services.publication_outbox_service.session_scope",
        lambda: _session_scope_for(db_session),
    )

    result = await service._deliver_one(delivery.id, repo._to_entity(recommendation))

    assert result["bucket"] == "success"
    notifier.post_notification_reply.assert_awaited_once_with(-1001, 501, f"{operation} update")
    assert db_session.query(PublicationDelivery).filter_by(id=delivery.id).one().status == PublicationDeliveryStatus.SENT.value


def test_outbox_allows_multiple_update_events_for_same_channel(db_session):
    recommendation = _recommendation(db_session)
    service = PublicationOutboxService(RecommendationRepository(), MagicMock())

    first = service.enqueue_operation(db_session, recommendation.id, [-1001], "UPDATE", "event:1")
    second = service.enqueue_operation(db_session, recommendation.id, [-1001], "UPDATE", "event:2")

    assert first[0].id != second[0].id
    assert db_session.query(PublicationDelivery).filter_by(
        recommendation_id=recommendation.id,
        telegram_channel_id=-1001,
        operation="UPDATE",
    ).count() == 2
