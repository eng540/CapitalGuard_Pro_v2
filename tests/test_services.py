import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from decimal import Decimal

from capitalguard.application.services.trade_service import TradeService
from capitalguard.domain.entities import UserType, RecommendationStatus
from capitalguard.infrastructure.db.models import User

# This suite tests the TradeService in isolation.

@pytest.fixture
def mock_market_data_service() -> MagicMock:
    return MagicMock()

@pytest.fixture
def mock_price_service() -> MagicMock:
    service = MagicMock()
    service.get_cached_price = AsyncMock(return_value=Decimal("50000.0"))
    return service

@pytest.fixture
def mock_alert_service() -> MagicMock:
    service = MagicMock()
    service.build_triggers_index = AsyncMock()
    return service

@pytest.fixture
def mock_notifier() -> MagicMock:
    notifier = MagicMock()
    notifier.post_to_channel = AsyncMock(return_value=(12345, 67890))
    # Add a mock for the bot_username attribute
    notifier.bot_username = "test_bot"
    return notifier

@pytest.fixture
def mock_repo() -> MagicMock:
    """A mock for the RecommendationRepository."""
    return MagicMock()

@pytest.fixture
def trade_service(
    mock_repo: MagicMock,
    mock_notifier: MagicMock,
    mock_market_data_service: MagicMock,
    mock_price_service: MagicMock,
    mock_alert_service: MagicMock
) -> TradeService:
    """Provides a TradeService instance with mocked dependencies."""
    service = TradeService(
        repo=mock_repo,
        notifier=mock_notifier,
        market_data_service=mock_market_data_service,
        price_service=mock_price_service
    )
    service.alert_service = mock_alert_service
    return service

@pytest.fixture
def mock_db_session() -> MagicMock:
    """Provides a MagicMock for the database session."""
    return MagicMock()

@pytest.mark.asyncio
async def test_create_and_publish_happy_path(trade_service: TradeService, mock_notifier: AsyncMock, mock_db_session: MagicMock):
    # Arrange
    mock_user = User(id=1, telegram_user_id=111, user_type=UserType.ANALYST)
    mock_channel = MagicMock()
    mock_channel.telegram_channel_id = 12345
    
    # Since the service now calls repositories directly, we patch them.
    with patch('capitalguard.application.services.trade_service.UserRepository') as mock_user_repo:
        mock_user_repo.return_value.find_by_telegram_id.return_value = mock_user
        with patch('capitalguard.application.services.trade_service.ChannelRepository') as mock_channel_repo:
            mock_channel_repo.return_value.list_by_analyst.return_value = [mock_channel]
            # Mock the method that returns the entity from the ORM object
            trade_service.repo._to_entity = MagicMock(return_value=MagicMock(status=RecommendationStatus.ACTIVE, asset=MagicMock(value="BTCUSDT"), id=99, is_shadow=False))

            kwargs = {
                "asset": "BTCUSDT", "side": "LONG", "entry": "50000",
                "stop_loss": "49000", "targets": [{"price": "51000"}],
                "order_type": "MARKET"
            }

            # Act
            rec_entity, report = await trade_service.create_and_publish_recommendation_async(
                user_id="111",
                db_session=mock_db_session,
                **kwargs
            )

    # Assert
    assert rec_entity is not None
    assert report["success"]
    mock_db_session.add.assert_called()
    mock_db_session.flush.assert_called()
    mock_notifier.post_to_channel.assert_called_once()
    trade_service.alert_service.build_triggers_index.assert_called_once()

@pytest.mark.asyncio
async def test_create_fails_if_user_is_not_analyst(trade_service: TradeService, mock_db_session: MagicMock):
    # Arrange
    mock_user = User(id=1, telegram_user_id=222, user_type=UserType.TRADER)
    
    with patch('capitalguard.application.services.trade_service.UserRepository') as mock_user_repo:
        mock_user_repo.return_value.find_by_telegram_id.return_value = mock_user

        kwargs = {
            "asset": "BTCUSDT", "side": "LONG", "entry": "50000",
            "stop_loss": "49000", "targets": [{"price": "51000"}],
            "order_type": "LIMIT"
        }
    
        # Act & Assert
        with pytest.raises(ValueError, match="Only analysts can create recommendations"):
            await trade_service.create_and_publish_recommendation_async(
                user_id="222",
                db_session=mock_db_session,
                **kwargs
            )
    
    trade_service.notifier.post_to_channel.assert_not_called()
    trade_service.alert_service.build_triggers_index.assert_not_called()