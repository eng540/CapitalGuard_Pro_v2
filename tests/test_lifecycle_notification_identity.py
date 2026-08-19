from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from capitalguard.application.services.lifecycle_service import LifecycleService
from capitalguard.infrastructure.db.models import UserTrade, UserTradeStatusEnum
from capitalguard.infrastructure.db.repository import UserRepository, RecommendationRepository


pytestmark = pytest.mark.asyncio


class CaptureOutbox:
    def __init__(self):
        self.calls = []

    def enqueue_operation(self, session, rec_id, channel_ids, operation, event_key, payload=None):
        self.calls.append({
            "rec_id": rec_id,
            "channel_ids": channel_ids,
            "operation": operation,
            "payload": payload or {},
        })


async def test_channel_lifecycle_reply_contains_recommendation_identity(db_session):
    repo = MagicMock()
    repo.get.return_value = SimpleNamespace(
        id=41,
        public_ref="REC-TEST-41",
        analyst=SimpleNamespace(analyst_code="AN-000041"),
    )
    repo.get_published_messages.return_value = [
        SimpleNamespace(telegram_channel_id=-10041, telegram_message_id=9001),
    ]
    outbox = CaptureOutbox()
    service = LifecycleService(repo=repo, notifier=MagicMock(), outbox_service=outbox)

    await service.notify_reply(41, "🎯 Hit TP2 at 65500", db_session)

    assert len(outbox.calls) == 1
    payload_text = outbox.calls[0]["payload"]["text"]
    assert "REC-TEST-41" in payload_text
    assert "AN-000041" in payload_text


async def test_private_trade_event_contains_trade_source_and_mode(db_session):
    user = UserRepository(db_session).find_or_create(telegram_id=94101, first_name="Notify")
    user.is_active = True
    trade = UserTrade(
        user_id=user.id,
        asset="BTCUSDT",
        side="LONG",
        entry=100,
        stop_loss=95,
        targets=[{"price": "105", "close_percent": 100.0}],
        status=UserTradeStatusEnum.ACTIVATED,
        source_type="DIRECT_INPUT",
    )
    db_session.add(trade)
    db_session.commit()

    service = LifecycleService(repo=RecommendationRepository(), notifier=MagicMock())
    service._notify_user_trade_update = AsyncMock()

    await service._notify_trade_event(
        db_session,
        trade,
        "✋ Trade Closed Manually",
        "Exit: <code>101</code>",
        mode="MANUAL",
    )

    text = service._notify_user_trade_update.await_args.args[1]
    assert "UserTrade:" in text
    assert "Trader Log" in text
    assert "MANUAL" in text
