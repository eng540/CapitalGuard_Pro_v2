from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from capitalguard.application.services.web_command_service import WebCommandError, WebCommandService
from capitalguard.infrastructure.db.models import UserTrade, UserTradeStatus
from capitalguard.infrastructure.db.repository import UserRepository


pytestmark = pytest.mark.asyncio


def _trade(db_session, telegram_id: int, public_ref: str, status=UserTradeStatus.ACTIVATED):
    user = UserRepository(db_session).find_or_create(telegram_id=telegram_id, first_name="Command")
    user.is_active = True
    trade = UserTrade(
        user_id=user.id,
        public_ref=public_ref,
        asset="BTCUSDT",
        side="LONG",
        entry=Decimal("100"),
        stop_loss=Decimal("95"),
        targets=[],
        status=status,
    )
    db_session.add(trade)
    db_session.commit()
    return trade


def _lifecycle(trade):
    async def close(_telegram_id, _trade_id, price, _session):
        trade.status = UserTradeStatus.CLOSED
        trade.close_price = price
        return trade

    return SimpleNamespace(close_user_trade_async=AsyncMock(side_effect=close))


async def test_close_command_replays_same_key_without_second_market_price(db_session):
    trade = _trade(db_session, 83001, "USR-083001/T-0001")
    lifecycle = _lifecycle(trade)
    prices = SimpleNamespace(get_cached_price=AsyncMock(return_value=110.0))
    service = WebCommandService()

    first = await service.close_user_trade(db_session, actor_telegram_id=83001, public_ref=trade.public_ref, idempotency_key="close-once", lifecycle_service=lifecycle, price_service=prices)
    replay = await service.close_user_trade(db_session, actor_telegram_id=83001, public_ref=trade.public_ref, idempotency_key="close-once", lifecycle_service=lifecycle, price_service=prices)

    assert first["replayed"] is False
    assert replay == first
    assert lifecycle.close_user_trade_async.await_count == 1
    assert prices.get_cached_price.await_count == 1


async def test_close_command_rejects_key_reuse_for_different_target(db_session):
    first_trade = _trade(db_session, 83002, "USR-083002/T-0001")
    second_trade = _trade(db_session, 83002, "USR-083002/T-0002")
    lifecycle = _lifecycle(first_trade)
    prices = SimpleNamespace(get_cached_price=AsyncMock(return_value=110.0))
    service = WebCommandService()

    await service.close_user_trade(db_session, actor_telegram_id=83002, public_ref=first_trade.public_ref, idempotency_key="shared-key", lifecycle_service=lifecycle, price_service=prices)
    with pytest.raises(WebCommandError, match="Idempotency key cannot be reused"):
        await service.close_user_trade(db_session, actor_telegram_id=83002, public_ref=second_trade.public_ref, idempotency_key="shared-key", lifecycle_service=lifecycle, price_service=prices)


async def test_close_command_rejects_foreign_or_closed_trade_before_price_lookup(db_session):
    foreign = _trade(db_session, 83003, "USR-083003/T-0001")
    own_closed = _trade(db_session, 83004, "USR-083004/T-0001", UserTradeStatus.CLOSED)
    lifecycle = _lifecycle(foreign)
    prices = SimpleNamespace(get_cached_price=AsyncMock(return_value=110.0))
    service = WebCommandService()

    with pytest.raises(WebCommandError, match="was not found"):
        await service.close_user_trade(db_session, actor_telegram_id=83004, public_ref=foreign.public_ref, idempotency_key="foreign-key", lifecycle_service=lifecycle, price_service=prices)
    with pytest.raises(WebCommandError, match="already closed"):
        await service.close_user_trade(db_session, actor_telegram_id=83004, public_ref=own_closed.public_ref, idempotency_key="closed-key", lifecycle_service=lifecycle, price_service=prices)
    assert prices.get_cached_price.await_count == 0


async def test_close_command_leaves_trade_unchanged_when_price_is_unavailable(db_session):
    trade = _trade(db_session, 83005, "USR-083005/T-0001")
    lifecycle = _lifecycle(trade)
    prices = SimpleNamespace(get_cached_price=AsyncMock(return_value=None))

    with pytest.raises(WebCommandError, match="Trusted market price is unavailable"):
        await WebCommandService().close_user_trade(db_session, actor_telegram_id=83005, public_ref=trade.public_ref, idempotency_key="no-price-key", lifecycle_service=lifecycle, price_service=prices)
    assert trade.status == UserTradeStatus.ACTIVATED
    assert lifecycle.close_user_trade_async.await_count == 0
