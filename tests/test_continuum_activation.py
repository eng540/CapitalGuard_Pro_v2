from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from capitalguard.application.services.lifecycle_service import LifecycleService
from capitalguard.application.services.web_command_service import WebCommandError, WebCommandService
from capitalguard.domain.entities import UserType
from capitalguard.infrastructure.db.models import UserTrade, UserTradeEvent, UserTradeStatusEnum, WebCommandAudit
from capitalguard.infrastructure.db.repository import RecommendationRepository, UserRepository


pytestmark = pytest.mark.asyncio


def _pending_trade(db_session, telegram_id: int, public_ref: str):
    user = UserRepository(db_session).find_or_create(
        telegram_id=telegram_id,
        user_type=UserType.ANALYST,
        first_name="Continuum Activation",
    )
    user.is_active = True
    trade = UserTrade(
        user_id=user.id,
        public_ref=public_ref,
        asset="BTCUSDT",
        side="LONG",
        entry=Decimal("100"),
        stop_loss=Decimal("95"),
        targets=[{"price": "110", "close_percent": 100}],
        status=UserTradeStatusEnum.PENDING_ACTIVATION,
        source_type="CONTINUUM_HANDOFF",
    )
    db_session.add(trade)
    db_session.commit()
    return trade


async def test_lifecycle_activation_requires_entry_and_records_explicit_event(db_session):
    trade = _pending_trade(db_session, 99301, "USR-99301/T-0001")
    lifecycle = LifecycleService(RecommendationRepository(), notifier=None)
    lifecycle._notify_trade_event = AsyncMock()
    lifecycle._commit_and_dispatch = AsyncMock()

    with pytest.raises(ValueError, match="Entry condition"):
        await lifecycle.activate_pending_user_trade_async(
            "99301", trade.id, Decimal("99"), db_session,
        )

    activated = await lifecycle.activate_pending_user_trade_async(
        "99301", trade.id, Decimal("101"), db_session,
    )

    assert activated.status is UserTradeStatusEnum.ACTIVATED
    assert activated.activated_at is not None
    event = db_session.query(UserTradeEvent).filter(UserTradeEvent.user_trade_id == trade.id).one()
    assert event.event_type == "ACTIVATED"
    assert event.event_data["mode"] == "EXPLICIT_CONTINUUM"
    assert event.event_data["activation_price"] == "101"
    lifecycle._commit_and_dispatch.assert_awaited_once()


async def test_web_activation_command_is_idempotent_and_uses_core_price_once(db_session):
    trade = _pending_trade(db_session, 99302, "USR-99302/T-0001")
    lifecycle = SimpleNamespace(activate_pending_user_trade_async=AsyncMock(return_value=trade))
    prices = SimpleNamespace(get_cached_price=AsyncMock(return_value=101.0))
    service = WebCommandService()

    first = await service.activate_continuum_user_trade(
        db_session,
        actor_telegram_id=99302,
        public_ref=trade.public_ref,
        idempotency_key="continuum-activate-001",
        lifecycle_service=lifecycle,
        price_service=prices,
    )
    replay = await service.activate_continuum_user_trade(
        db_session,
        actor_telegram_id=99302,
        public_ref=trade.public_ref,
        idempotency_key="continuum-activate-001",
        lifecycle_service=lifecycle,
        price_service=prices,
    )

    assert first["status"] == UserTradeStatusEnum.PENDING_ACTIVATION.value
    assert first["live_activation"] is True
    assert replay == first
    assert lifecycle.activate_pending_user_trade_async.await_count == 1
    assert prices.get_cached_price.await_count == 1
    assert db_session.query(WebCommandAudit).filter_by(idempotency_key="continuum-activate-001").count() == 1


async def test_web_activation_rejects_non_continuum_trade_without_market_lookup(db_session):
    trade = _pending_trade(db_session, 99303, "USR-99303/T-0001")
    trade.source_type = "FORWARD"
    db_session.commit()
    lifecycle = SimpleNamespace(activate_pending_user_trade_async=AsyncMock())
    prices = SimpleNamespace(get_cached_price=AsyncMock(return_value=101.0))

    with pytest.raises(WebCommandError, match="Only a Continuum"):
        await WebCommandService().activate_continuum_user_trade(
            db_session,
            actor_telegram_id=99303,
            public_ref=trade.public_ref,
            idempotency_key="continuum-activate-002",
            lifecycle_service=lifecycle,
            price_service=prices,
        )

    prices.get_cached_price.assert_not_awaited()
    lifecycle.activate_pending_user_trade_async.assert_not_awaited()
