# tests/test_integration_flow.py (NEW FILE)
"""
High-level integration tests that simulate real user workflows.
This is the core of our new "Safety Net".
"""

import pytest
from decimal import Decimal

from capitalguard.infrastructure.db.repository import UserRepository
from capitalguard.application.services.trade_service import TradeService
from capitalguard.domain.entities import UserType

# Mark all tests in this file as asynchronous
pytestmark = pytest.mark.asyncio

@pytest.fixture
def trade_service(services) -> TradeService:
    return services["trade_service"]

async def test_analyst_promotion_and_rec_creation_flow(db_session, trade_service: TradeService):
    """
    **This test codifies the exact workflow that was failing repeatedly.**
    It will now fail immediately if any future change breaks this critical path.
    
    1. A new user is created (should be TRADER, inactive).
    2. Admin promotes the user to ANALYST.
    3. Admin activates the user.
    4. The new analyst attempts to create a recommendation.
    5. **ASSERT**: The creation must succeed.
    """
    # 1. Setup: Create a new user
    user_repo = UserRepository(db_session)
    new_user = user_repo.find_or_create(telegram_id=12345, first_name="Test")
    db_session.commit()
    
    assert new_user.user_type == UserType.TRADER
    assert not new_user.is_active

    # 2. Action: Promote to Analyst
    user_to_promote = user_repo.find_by_telegram_id(12345)
    user_to_promote.user_type = UserType.ANALYST.value
    db_session.commit()

    # 3. Action: Activate user
    user_to_activate = user_repo.find_by_telegram_id(12345)
    user_to_activate.is_active = True
    db_session.commit()

    # Verify state before the final test
    promoted_user = user_repo.find_by_telegram_id(12345)
    assert promoted_user.user_type == UserType.ANALYST
    assert promoted_user.is_active

    # 4. Action: Attempt to create a recommendation as the new analyst
    rec_data = {
        "asset": "BTCUSDT",
        "side": "LONG",
        "order_type": "LIMIT",
        "entry": Decimal("60000"),
        "stop_loss": Decimal("59000"),
        "targets": [{"price": Decimal("61000"), "close_percent": 100.0}]
    }
    
    # 5. ASSERT: This call must succeed and not raise a ValueError.
    try:
        created_rec, report = await trade_service.create_and_publish_recommendation_async(
            user_id=str(promoted_user.telegram_user_id),
            db_session=db_session,
            **rec_data
        )
        assert created_rec is not None
        assert report == {"queued": True, "success": [], "failed": []}
    except ValueError as e:
        pytest.fail(f"CRITICAL REGRESSION: Analyst promotion flow failed. "
                    f"create_and_publish_recommendation_async raised an unexpected error: {e}")

async def test_duplicate_trade_tracking_prevention(db_session, trade_service: TradeService):
    """
    **This test codifies the duplicate tracking bug.**
    
    1. Create a base recommendation.
    2. A user tracks it for the first time (should succeed).
    3. The same user tries to track it again (should fail).
    """
    # 1. Setup: Create a user and a source recommendation
    user_repo = UserRepository(db_session)
    analyst = user_repo.find_or_create(telegram_id=111, first_name="Analyst", user_type=UserType.ANALYST)
    trader = user_repo.find_or_create(telegram_id=222, first_name="Trader")
    db_session.commit()

    rec_data = {
        "asset": "ETHUSDT", "side": "SHORT", "order_type": "LIMIT",
        "entry": Decimal("3000"), "stop_loss": Decimal("3100"),
        "targets": [{"price": Decimal("2900"), "close_percent": 100.0}]
    }
    source_rec, _ = await trade_service.create_and_publish_recommendation_async(
        user_id=str(analyst.telegram_user_id), db_session=db_session, **rec_data
    )
    db_session.commit()

    # 2. Action: First tracking attempt
    result1 = await trade_service.create_trade_from_recommendation(
        user_id=str(trader.telegram_user_id), rec_id=source_rec.id, db_session=db_session
    )
    db_session.commit()
    assert result1["success"] is True

    # 3. Action & ASSERT: Second tracking attempt must fail
    result2 = await trade_service.create_trade_from_recommendation(
        user_id=str(trader.telegram_user_id), rec_id=source_rec.id, db_session=db_session
    )
    assert result2["success"] is False
    assert result2["error"] == "You are already tracking this signal."


async def test_duplicate_forwarding_is_rejected_by_dedup_ledger(db_session, trade_service: TradeService):
    user_repo = UserRepository(db_session)
    trader = user_repo.find_or_create(telegram_id=333, first_name="Forwarder")
    trader.is_active = True
    db_session.commit()

    trade_data = {
        "asset": "BTCUSDT",
        "side": "LONG",
        "entry": Decimal("60000"),
        "stop_loss": Decimal("59000"),
        "targets": [{"price": Decimal("61000"), "close_percent": 100.0}],
    }
    kwargs = {
        "user_id": str(trader.telegram_user_id),
        "trade_data": trade_data,
        "original_text": "BTCUSDT LONG Entry 60000 SL 59000 TP 61000",
        "db_session": db_session,
        "status_to_set": "WATCHLIST",
        "original_published_at": None,
        "channel_info": {"id": 987654, "title": "Test Channel"},
    }

    first = await trade_service.create_trade_from_forwarding_async(**kwargs)
    db_session.commit()
    second = await trade_service.create_trade_from_forwarding_async(**kwargs)

    assert first["success"] is True
    assert second["success"] is False
    assert second["duplicate"] is True
    assert "Duplicate signal" in second["error"]


async def test_direct_input_log_creates_watchlist_and_source_type(db_session, trade_service: TradeService):
    user_repo = UserRepository(db_session)
    trader = user_repo.find_or_create(telegram_id=444, first_name="DirectLogger")
    trader.is_active = True
    db_session.commit()

    trade_data = {
        "asset": "SOLUSDT",
        "side": "LONG",
        "entry": Decimal("150"),
        "stop_loss": Decimal("140"),
        "targets": [{"price": Decimal("160"), "close_percent": 100.0}],
        "source_type": "DIRECT_INPUT",
    }
    kwargs = {
        "user_id": str(trader.telegram_user_id),
        "trade_data": trade_data,
        "original_text": "SOLUSDT LONG 150 140 160",
        "db_session": db_session,
        "status_to_set": "WATCHLIST",
        "original_published_at": None,
        "channel_info": None,
    }

    result = await trade_service.create_trade_from_forwarding_async(**kwargs)
    db_session.commit()
    assert result["success"] is True

    from capitalguard.infrastructure.db.models import UserTrade
    saved = db_session.get(UserTrade, result["trade_id"])
    assert saved is not None
    assert saved.source_type == "DIRECT_INPUT"
    assert any(event.event_type == "LOGGED_DIRECT_INPUT" for event in saved.events)
    assert saved.status.value == "WATCHLIST"
    assert saved.watched_channel_id is None

    duplicate = await trade_service.create_trade_from_forwarding_async(**kwargs)
    assert duplicate["success"] is False
    assert duplicate["duplicate"] is True
