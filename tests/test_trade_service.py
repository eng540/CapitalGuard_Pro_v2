# --- tests/test_trade_service.py ---
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, ANY, AsyncMock # Import AsyncMock
from datetime import datetime

# Application specific imports
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.creation_service import CreationService
from capitalguard.application.services.lifecycle_service import LifecycleService
from capitalguard.infrastructure.db.repository import UserRepository, RecommendationRepository # Needed for setup/verification
from capitalguard.infrastructure.db.models import User, UserTrade, UserTradeEvent, UserTradeStatus, Recommendation, RecommendationStatusEnum # Import ORM models
from capitalguard.domain.entities import (
    UserType,
    Recommendation as RecommendationEntity,
    RecommendationStatus as RecommendationStatusEntity,
)
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
    creation_service = CreationService(
        repo=repo,
        notifier=mock_notifier,
        market_data_service=mock_market_data_service,
        price_service=mock_price_service,
    )
    lifecycle_service = LifecycleService(repo=repo, notifier=mock_notifier)
    service = TradeService(
        repo=repo,
        notifier=mock_notifier,
        market_data_service=mock_market_data_service,
        price_service=mock_price_service,
        creation_service=creation_service,
        lifecycle_service=lifecycle_service,
    )
    service.alert_service = MagicMock()
    service.alert_service.build_triggers_index = AsyncMock()
    service.alert_service.build_trigger_data_from_orm.return_value = None
    service.alert_service.add_trigger_data = AsyncMock()
    service.alert_service.remove_single_trigger = AsyncMock()
    creation_service.alert_service = service.alert_service
    creation_service.lifecycle_service = lifecycle_service
    lifecycle_service.alert_service = service.alert_service
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
        db_session=db_session,
        status_to_set="WATCHLIST",
        original_published_at=None,
        channel_info=None,
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
    assert saved_trade.status == UserTradeStatus.WATCHLIST
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
    user_db_id = user.id

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
        db_session=db_session,
        status_to_set="WATCHLIST",
        original_published_at=None,
        channel_info=None,
    )

    # Assert
    assert result['success'] is False
    assert "LONG SL must be < Entry" in result['error']
    # Verify no trade was saved
    count = db_session.query(UserTrade).filter(UserTrade.user_id == user_db_id).count()
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
        status=UserTradeStatus.ACTIVATED
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

    trade_a = UserTrade(user_id=user_a.id, asset="LINKUSDT", side="LONG", entry=Decimal("20"), stop_loss=Decimal("19"), targets=[{"price":"21"}], status=UserTradeStatus.ACTIVATED)
    db_session.add(trade_a)
    db_session.commit()

    # Act & Assert: User B attempts to close User A's trade
    with pytest.raises(ValueError, match="Trade #.* not found"):
        await trade_service_real_db.close_user_trade_async(
            user_id=str(user_b.telegram_user_id), # User B's ID
            trade_id=trade_a.id,
            exit_price=Decimal("21"),
            db_session=db_session
        )
    # Verify trade A is still open
    db_session.expire(trade_a)
    reloaded_trade_a = db_session.query(UserTrade).filter(UserTrade.id == trade_a.id).first()
    assert reloaded_trade_a.status == UserTradeStatus.ACTIVATED

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
    assert result_orm.pnl_percentage == Decimal("5.0") # Original PnL calculation


async def test_partial_close_user_trade_records_remaining_size_and_event(trade_service_real_db: TradeService, db_session):
    user = UserRepository(db_session).find_or_create(telegram_id=556, first_name="Partial")
    user.is_active = True
    trade = UserTrade(
        user_id=user.id, asset="BTCUSDT", side="LONG", entry=Decimal("100"),
        stop_loss=Decimal("95"), targets=[{"price": "110", "close_percent": 100.0}],
        status=UserTradeStatus.ACTIVATED, open_size_percent=Decimal("100"),
    )
    db_session.add(trade)
    db_session.commit()

    result = await trade_service_real_db.partial_close_user_trade_async(
        user_id=str(user.telegram_user_id), trade_id=trade.id,
        close_percent=Decimal("25"), exit_price=Decimal("110"), db_session=db_session,
    )

    assert result.status == UserTradeStatus.ACTIVATED
    assert result.open_size_percent == Decimal("75")
    assert result.close_price is None
    assert result.pnl_percentage is None
    event = db_session.query(UserTradeEvent).filter(
        UserTradeEvent.user_trade_id == trade.id,
        UserTradeEvent.event_type == "MANUAL_PARTIAL_CLOSE",
    ).one()
    assert event.event_data == {
        "price": "110", "amount": 25.0, "pnl": 10.0,
        "remaining_open_size_percent": 75.0, "mode": "MANUAL",
    }


async def test_partial_close_user_trade_rejects_full_or_foreign_trade(trade_service_real_db: TradeService, db_session):
    owner = UserRepository(db_session).find_or_create(telegram_id=557, first_name="PartialOwner")
    other = UserRepository(db_session).find_or_create(telegram_id=558, first_name="PartialOther")
    trade = UserTrade(
        user_id=owner.id, asset="ETHUSDT", side="LONG", entry=Decimal("100"),
        stop_loss=Decimal("95"), targets=[], status=UserTradeStatus.ACTIVATED,
        open_size_percent=Decimal("30"),
    )
    db_session.add(trade)
    db_session.commit()

    with pytest.raises(ValueError, match="less than the remaining"):
        await trade_service_real_db.partial_close_user_trade_async(
            user_id=str(owner.telegram_user_id), trade_id=trade.id,
            close_percent=Decimal("30"), exit_price=Decimal("110"), db_session=db_session,
        )
    with pytest.raises(ValueError, match="Trade #.* not found"):
        await trade_service_real_db.partial_close_user_trade_async(
            user_id=str(other.telegram_user_id), trade_id=trade.id,
            close_percent=Decimal("10"), exit_price=Decimal("110"), db_session=db_session,
        )
    db_session.refresh(trade)
    assert trade.open_size_percent == Decimal("30")

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

    # Publishing is intentionally queued for the background worker.
    assert report == {"queued": True, "success": [], "failed": []}

    # Verify DB state
    saved_rec_orm = db_session.query(Recommendation).filter(Recommendation.id == created_rec_entity.id).first()
    assert saved_rec_orm is not None
    assert saved_rec_orm.analyst_id == analyst.id
    assert saved_rec_orm.status == RecommendationStatusEnum.PENDING


# --- END of test_trade_service.py update ---


async def test_trader_history_includes_closed_user_trade(trade_service_real_db: TradeService, db_session):
    user = UserRepository(db_session).find_or_create(telegram_id=92301, first_name="HistoryTrader")
    user.is_active = True
    closed_trade = UserTrade(
        user_id=user.id,
        asset="SOLUSDT",
        side="LONG",
        entry=Decimal("100"),
        stop_loss=Decimal("95"),
        targets=[{"price": "105", "close_percent": 100.0}],
        status=UserTradeStatus.CLOSED,
        close_price=Decimal("105"),
        pnl_percentage=Decimal("5.0"),
    )
    db_session.add(closed_trade)
    db_session.commit()

    history = trade_service_real_db.get_history_for_user(
        db_session,
        str(user.telegram_user_id),
    )

    assert len(history) == 1
    item = history[0]
    assert item.is_user_trade is True
    assert item.unified_status == "CLOSED"
    assert item.exit_price == 105.0
    assert item.final_pnl_percentage == 5.0
