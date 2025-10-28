# --- tests/test_trade_service.py ---
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, ANY, AsyncMock # Import AsyncMock
from datetime import datetime

# Application specific imports
from capitalguard.application.services.trade_service import TradeService
from capitalguard.infrastructure.db.repository import UserRepository, RecommendationRepository # Needed for setup/verification
from capitalguard.infrastructure.db.models import User, UserTrade, UserTradeStatus, Recommendation, RecommendationStatusEnum # Import ORM models
from capitalguard.domain.entities import UserType, Recommendation as RecommendationEntity # Import domain enums/entities
from capitalguard.domain.value_objects import Symbol, Side, Price, Targets # Import Value Objects

# Mark tests as async
pytestmark = pytest.mark.asyncio

# --- Fixtures ---

@pytest.fixture
def mock_notifier() -> MagicMock:
    """Provides a mock TelegramNotifier with async methods."""
    notifier = MagicMock()
    # Mock methods used by TradeService as async
    notifier.post_to_channel = AsyncMock(return_value=(12345, 67890)) # Simulate success: (chat_id, msg_id)
    notifier.edit_recommendation_card_by_ids = AsyncMock(return_value=True)
    notifier.post_notification_reply = AsyncMock() # Doesn't need a specific return value
    return notifier

@pytest.fixture
def mock_market_data_service() -> MagicMock:
    """Provides a mock MarketDataService."""
    service = MagicMock()
    service.is_valid_symbol.return_value = True # Assume all symbols are valid for tests
    return service

@pytest.fixture
def mock_price_service() -> MagicMock:
    """Provides a mock PriceService with async methods."""
    service = MagicMock()
    # Mock get_cached_price as an async function
    service.get_cached_price = AsyncMock(return_value=Decimal("60500.0")) # Default mock price
    return service

@pytest.fixture
def trade_service_real_db(
    db_session, # Use the real DB session fixture
    mock_notifier: MagicMock,
    mock_market_data_service: MagicMock,
    mock_price_service: MagicMock
) -> TradeService:
    """
    Provides a TradeService instance using a real DB session
    but with mocked external dependencies (notifier, price, market data).
    """
    # Instantiate the real repository using the test session
    repo = RecommendationRepository()
    # Instantiate TradeService with real repo and mock externals
    service = TradeService(
        repo=repo,
        notifier=mock_notifier,
        market_data_service=mock_market_data_service,
        price_service=mock_price_service
    )
    # Mock alert_service if needed, or set to None
    service.alert_service = AsyncMock()
    service.alert_service.build_triggers_index = AsyncMock()
    return service

# --- Test Cases ---

async def test_create_trade_from_forwarding_success(trade_service_real_db: TradeService, db_session):
    """Tests successfully creating a UserTrade from parsed data."""
    # Arrange: Create a user
    user_repo = UserRepository(db_session)
    user = user_repo.find_or_create(telegram_id=999, first_name="Forwarder")
    user.is_active = True
    db_session.commit()

    trade_data = {
        "asset": "ADAUSDT", "side": "LONG",
        "entry": Decimal("1.5"), "stop_loss": Decimal("1.4"),
        "targets": [{"price": Decimal("1.6"), "close_percent": 50.0}, {"price": Decimal("1.7"), "close_percent": 50.0}]
    }
    original_text = "Forwarded: ADA LONG Entry 1.5 SL 1.4 TP 1.6@50 1.7@50"

    # Act
    result = await trade_service_real_db.create_trade_from_forwarding_async(
        user_id=str(user.telegram_user_id),
        trade_data=trade_data,
        original_text=original_text,
        db_session=db_session
    )

    # Assert
    assert result['success'] is True
    assert result['asset'] == "ADAUSDT"
    trade_id = result['trade_id']

    # Verify DB state
    saved_trade = db_session.query(UserTrade).filter(UserTrade.id == trade_id).first()
    assert saved_trade is not None
    assert saved_trade.user_id == user.id
    assert saved_trade.asset == "ADAUSDT"
    assert saved_trade.entry == Decimal("1.5")
    assert saved_trade.status == UserTradeStatus.OPEN
    assert saved_trade.source_forwarded_text == original_text
    assert len(saved_trade.targets) == 2
    assert saved_trade.targets[0]['price'] == '1.6' # Stored as string

async def test_create_trade_from_forwarding_validation_fail(trade_service_real_db: TradeService, db_session):
    """Tests UserTrade creation failure due to invalid data (e.g., SL wrong side)."""
    # Arrange: Create a user
    user_repo = UserRepository(db_session)
    user = user_repo.find_or_create(telegram_id=998, first_name="ForwardFail")
    user.is_active = True
    db_session.commit()

    invalid_trade_data = {
        "asset": "SOLUSDT", "side": "LONG",
        "entry": Decimal("150"), "stop_loss": Decimal("160"), # Invalid SL for LONG
        "targets": [{"price": Decimal("170"), "close_percent": 100.0}]
    }

    # Act
    result = await trade_service_real_db.create_trade_from_forwarding_async(
        user_id=str(user.telegram_user_id),
        trade_data=invalid_trade_data,
        original_text="Some text",
        db_session=db_session
    )

    # Assert
    assert result['success'] is False
    assert "Stop Loss must be less than Entry" in result['error']
    # Verify no trade was saved
    count = db_session.query(UserTrade).filter(UserTrade.user_id == user.id).count()
    assert count == 0


async def test_close_user_trade_success(trade_service_real_db: TradeService, db_session):
    """Tests successfully closing a UserTrade."""
    # Arrange: Create user and an open UserTrade
    user_repo = UserRepository(db_session)
    user = user_repo.find_or_create(telegram_id=888, first_name="Closer")
    user.is_active = True
    db_session.commit()

    open_trade = UserTrade(
        user_id=user.id, asset="DOTUSDT", side="SHORT",
        entry=Decimal("30"), stop_loss=Decimal("31"),
        targets=[{"price": "29", "close_percent": 100.0}], # Stored as string in DB
        status=UserTradeStatus.OPEN
    )
    db_session.add(open_trade)
    db_session.commit()
    trade_id = open_trade.id

    # Act: Close the trade
    exit_price = Decimal("29.5")
    closed_trade_orm = await trade_service_real_db.close_user_trade_async(
        user_id=str(user.telegram_user_id),
        trade_id=trade_id,
        exit_price=exit_price,
        db_session=db_session
    )

    # Assert: Service response (ORM object)
    assert closed_trade_orm is not None
    assert closed_trade_orm.id == trade_id
    assert closed_trade_orm.status == UserTradeStatus.CLOSED
    assert closed_trade_orm.close_price == exit_price
    assert closed_trade_orm.closed_at is not None
    # PnL for SHORT from 30 closed at 29.5 -> (30 / 29.5 - 1) * 100
    expected_pnl = (Decimal("30") / Decimal("29.5") - 1) * 100
    # Compare Decimal results carefully
    assert abs(closed_trade_orm.pnl_percentage - expected_pnl) < Decimal("0.0001") # Check PnL calculation

    # Assert: Verify DB state after commit (implicit via decorator/context)
    # Re-query within the same session or a new one to confirm persistence
    db_session.expire(closed_trade_orm) # Force reload from DB
    refreshed_trade = db_session.query(UserTrade).filter(UserTrade.id == trade_id).first()
    assert refreshed_trade.status == UserTradeStatus.CLOSED
    assert refreshed_trade.close_price == exit_price


async def test_close_user_trade_unauthorized(trade_service_real_db: TradeService, db_session):
    """Tests that a user cannot close another user's trade."""
    # Arrange: User A creates trade, User B tries to close it
    user_repo = UserRepository(db_session)
    user_a = user_repo.find_or_create(telegram_id=777, first_name="Owner")
    user_b = user_repo.find_or_create(telegram_id=666, first_name="Other")
    user_a.is_active = True; user_b.is_active = True
    db_session.commit()

    trade_a = UserTrade(user_id=user_a.id, asset="LINKUSDT", side="LONG", entry=Decimal("20"), stop_loss=Decimal("19"), targets=[{"price":"21"}], status=UserTradeStatus.OPEN)
    db_session.add(trade_a)
    db_session.commit()

    # Act & Assert: User B attempts to close User A's trade
    with pytest.raises(ValueError, match="Access denied"):
        await trade_service_real_db.close_user_trade_async(
            user_id=str(user_b.telegram_user_id), # User B's ID
            trade_id=trade_a.id,
            exit_price=Decimal("21"),
            db_session=db_session
        )
    # Verify trade A is still open
    db_session.expire(trade_a)
    reloaded_trade_a = db_session.query(UserTrade).filter(UserTrade.id == trade_a.id).first()
    assert reloaded_trade_a.status == UserTradeStatus.OPEN

async def test_close_already_closed_user_trade(trade_service_real_db: TradeService, db_session):
    """Tests that closing an already closed trade is idempotent."""
    # Arrange: Create user and a closed UserTrade
    user_repo = UserRepository(db_session)
    user = user_repo.find_or_create(telegram_id=555, first_name="Repeater")
    user.is_active = True
    db_session.commit()

    closed_trade = UserTrade(
        user_id=user.id, asset="AAVEUSDT", side="LONG",
        entry=Decimal("100"), stop_loss=Decimal("95"), targets=[{"price": "105"}],
        status=UserTradeStatus.CLOSED, close_price=Decimal("105"), pnl_percentage=Decimal("5.0")
    )
    db_session.add(closed_trade)
    db_session.commit()
    trade_id = closed_trade.id

    # Act: Try to close it again
    result_orm = await trade_service_real_db.close_user_trade_async(
        user_id=str(user.telegram_user_id),
        trade_id=trade_id,
        exit_price=Decimal("106"), # Different price
        db_session=db_session
    )

    # Assert: Should return the original closed trade without changes
    assert result_orm is not None
    assert result_orm.id == trade_id
    assert result_orm.status == UserTradeStatus.CLOSED
    assert result_orm.close_price == Decimal("105") # Original price
    assert result_orm.pnl_percentage == Decimal("5.0") # Original PnL

# --- Keep existing tests for Recommendation lifecycle (create, close, etc.) ---
# Example: Ensure create_and_publish still works
async def test_create_and_publish_recommendation_success(trade_service_real_db: TradeService, db_session, mock_notifier: MagicMock):
    """Tests successful creation and publication of an analyst Recommendation."""
    # Arrange: Create an analyst user
    user_repo = UserRepository(db_session)
    analyst = user_repo.find_or_create(telegram_id=111, first_name="Analyst", user_type=UserType.ANALYST)
    analyst.is_active = True
    db_session.commit()

    rec_data = {
        "asset": "BTCUSDT", "side": "LONG", "order_type": "LIMIT",
        "entry": Decimal("60000"), "stop_loss": Decimal("59000"),
        "targets": [{"price": Decimal("61000"), "close_percent": 100.0}],
        "notes": "Test note", "market": "Futures"
    }

    # Act
    created_rec_entity, report = await trade_service_real_db.create_and_publish_recommendation_async(
        user_id=str(analyst.telegram_user_id),
        db_session=db_session,
        **rec_data
    )

    # Assert
    assert created_rec_entity is not None
    assert created_rec_entity.id is not None
    assert created_rec_entity.asset.value == "BTCUSDT"
    assert created_rec_entity.status == RecommendationStatusEntity.PENDING # Limit order starts pending
    assert created_rec_entity.analyst_id == analyst.id

    # Check that notifier was called (assuming mock setup implies channel linking)
    # Depending on _publish_recommendation logic, check call count or specific calls
    # For now, just check if it was called at least once
    mock_notifier.post_to_channel.assert_called() # Check if post was attempted

    # Verify DB state
    saved_rec_orm = db_session.query(Recommendation).filter(Recommendation.id == created_rec_entity.id).first()
    assert saved_rec_orm is not None
    assert saved_rec_orm.analyst_id == analyst.id
    assert saved_rec_orm.status == RecommendationStatusEnum.PENDING


# --- END of test_trade_service.py update ---