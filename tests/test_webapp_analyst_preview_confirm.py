from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from capitalguard.application.services.creation_service import CreationService
from capitalguard.application.services.publication_outbox_service import PublicationOutboxService
from capitalguard.application.services.web_command_service import WebCommandError, WebCommandService
from capitalguard.domain.entities import UserType as UserTypeEntity
from capitalguard.infrastructure.db.models import Channel, PublicationDelivery, Recommendation, WebCommandAudit
from capitalguard.infrastructure.db.repository import RecommendationRepository, UserRepository


pytestmark = pytest.mark.asyncio


def _creation_service(price=105.0, with_outbox=False):
    notifier = MagicMock()
    outbox = PublicationOutboxService(RecommendationRepository(), notifier) if with_outbox else None
    return CreationService(
        RecommendationRepository(),
        notifier,
        MagicMock(),
        MagicMock(get_cached_price=AsyncMock(return_value=price)),
        outbox_service=outbox,
    )


def _analyst_and_channel(db_session, telegram_id=874001):
    analyst = UserRepository(db_session).find_or_create(
        telegram_id=telegram_id,
        first_name="Preview Analyst",
        user_type=UserTypeEntity.ANALYST,
        is_active=True,
    )
    channel = Channel(
        analyst_id=analyst.id,
        telegram_channel_id=-1000000000000 - telegram_id,
        title="Preview Channel",
        is_active=True,
    )
    db_session.add(channel)
    db_session.flush()
    return analyst, channel


def _recommendation_payload(channel_id):
    return {
        "asset": "BTCUSDT",
        "side": "LONG",
        "market": "Futures",
        "order_type": "LIMIT",
        "entry": Decimal("100"),
        "stop_loss": Decimal("95"),
        "targets": [{"price": Decimal("110"), "close_percent": 100}],
        "notes": "Preview contract test",
        "target_channel_ids": {channel_id},
    }


async def test_preview_is_read_only_and_returns_not_queued_publication(db_session):
    analyst, channel = _analyst_and_channel(db_session)
    service = _creation_service()
    before = (
        db_session.query(Recommendation).count(),
        db_session.query(PublicationDelivery).count(),
        db_session.query(WebCommandAudit).count(),
    )

    preview = await service.preview_recommendation_async(
        str(analyst.telegram_user_id), db_session, **_recommendation_payload(channel.telegram_channel_id)
    )

    assert preview["mode"] == "PREVIEW"
    assert preview["entry"] == "100"
    assert preview["publication"] == {"state": "NOT_QUEUED", "eligible_channel_count": 1}
    assert before == (
        db_session.query(Recommendation).count(),
        db_session.query(PublicationDelivery).count(),
        db_session.query(WebCommandAudit).count(),
    )


async def test_preview_uses_core_market_price_and_rejects_invalid_financial_shape(db_session):
    analyst, channel = _analyst_and_channel(db_session, 874002)
    service = _creation_service(price=102.5)
    market_payload = _recommendation_payload(channel.telegram_channel_id)
    market_payload.update({"order_type": "MARKET", "entry": Decimal("100"), "stop_loss": Decimal("95"), "targets": [{"price": Decimal("110"), "close_percent": 100}]})

    preview = await service.preview_recommendation_async(str(analyst.telegram_user_id), db_session, **market_payload)
    assert preview["entry"] == "102.5"
    assert preview["live_price"] == "102.5"
    service.price_service.get_cached_price.assert_awaited_once_with("BTCUSDT", "Futures", True)

    invalid_payload = _recommendation_payload(channel.telegram_channel_id)
    invalid_payload["targets"] = [{"price": Decimal("99"), "close_percent": 100}]
    with pytest.raises(ValueError, match="LONG targets"):
        await service.preview_recommendation_async(str(analyst.telegram_user_id), db_session, **invalid_payload)


async def test_preview_rejects_foreign_or_inactive_channel_without_writing(db_session):
    analyst, channel = _analyst_and_channel(db_session, 874003)
    foreign = Channel(analyst_id=analyst.id + 999, telegram_channel_id=-100999, title="Foreign", is_active=True)
    inactive = Channel(analyst_id=analyst.id, telegram_channel_id=-100998, title="Inactive", is_active=False)
    db_session.add_all([foreign, inactive])
    db_session.flush()
    service = _creation_service()

    for channel_id in (foreign.telegram_channel_id, inactive.telegram_channel_id):
        payload = _recommendation_payload(channel_id)
        with pytest.raises(ValueError, match="not active"):
            await service.preview_recommendation_async(str(analyst.telegram_user_id), db_session, **payload)
    assert db_session.query(Recommendation).count() == 0


async def test_confirm_is_idempotent_and_queues_one_delivery(db_session):
    analyst, channel = _analyst_and_channel(db_session, 874004)
    creation_service = _creation_service(with_outbox=True)
    command = WebCommandService()
    payload = _recommendation_payload(channel.telegram_channel_id)

    first = await command.confirm_analyst_recommendation(
        db_session,
        actor_telegram_id=analyst.telegram_user_id,
        idempotency_key="analyst-confirm-key-0001",
        creation_service=creation_service,
        recommendation=payload,
    )
    replay = await command.confirm_analyst_recommendation(
        db_session,
        actor_telegram_id=analyst.telegram_user_id,
        idempotency_key="analyst-confirm-key-0001",
        creation_service=creation_service,
        recommendation=payload,
    )

    assert replay == first
    assert set(first) == {"ok", "entity_type", "public_ref", "publication", "replayed"}
    assert first["publication"] == {"state": "QUEUED", "queued_delivery_count": 1}
    assert db_session.query(Recommendation).count() == 1
    assert db_session.query(PublicationDelivery).count() == 1
    assert db_session.query(WebCommandAudit).count() == 1

    changed_payload = {**payload, "entry": Decimal("101")}
    with pytest.raises(WebCommandError, match="cannot be reused"):
        await command.confirm_analyst_recommendation(
            db_session,
            actor_telegram_id=analyst.telegram_user_id,
            idempotency_key="analyst-confirm-key-0001",
            creation_service=creation_service,
            recommendation=changed_payload,
        )
