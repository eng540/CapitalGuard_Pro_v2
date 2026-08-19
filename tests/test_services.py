import pytest
from unittest.mock import AsyncMock, MagicMock

from capitalguard.application.services.trade_service import TradeService


@pytest.fixture
def trade_service_facade():
    creation_service = MagicMock()
    lifecycle_service = MagicMock()
    creation_service.create_and_publish_recommendation_async = AsyncMock()
    lifecycle_service.close_recommendation_async = AsyncMock()
    service = TradeService(
        repo=MagicMock(),
        notifier=MagicMock(),
        market_data_service=MagicMock(),
        price_service=MagicMock(),
        creation_service=creation_service,
        lifecycle_service=lifecycle_service,
    )
    return service, creation_service, lifecycle_service


@pytest.mark.asyncio
async def test_trade_service_delegates_recommendation_creation(trade_service_facade):
    service, creation_service, _ = trade_service_facade
    expected = (MagicMock(id=99), {"queued": True, "success": [], "failed": []})
    creation_service.create_and_publish_recommendation_async.return_value = expected

    result = await service.create_and_publish_recommendation_async(
        user_id="123",
        db_session=MagicMock(),
        asset="BTCUSDT",
        side="LONG",
        order_type="LIMIT",
        entry=50000,
        stop_loss=49000,
        targets=[{"price": 51000, "close_percent": 100}],
    )

    assert result == expected
    creation_service.create_and_publish_recommendation_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_trade_service_delegates_recommendation_close(trade_service_facade):
    service, _, lifecycle_service = trade_service_facade
    expected = MagicMock(id=101)
    lifecycle_service.close_recommendation_async.return_value = expected

    result = await service.close_recommendation_async(
        rec_id=101,
        user_id="123",
        exit_price=50000,
        db_session=MagicMock(),
        reason="TEST",
    )

    assert result == expected
    lifecycle_service.close_recommendation_async.assert_awaited_once()
