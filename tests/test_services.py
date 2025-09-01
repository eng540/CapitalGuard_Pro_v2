# --- START OF FILE: tests/test_services.py ---
import pytest
from unittest.mock import MagicMock, ANY
from types import SimpleNamespace
from datetime import datetime

from capitalguard.application.services.trade_service import TradeService
from capitalguard.domain.entities import Recommendation
from capitalguard.domain.value_objects import Symbol, Side, Price, Targets

@pytest.fixture
def mock_repo() -> MagicMock:
    """A mock for the RecommendationRepository."""
    return MagicMock()

@pytest.fixture
def mock_notifier() -> MagicMock:
    """A mock for the NotifierPort."""
    return MagicMock()

@pytest.fixture
def trade_service(mock_repo: MagicMock, mock_notifier: MagicMock) -> TradeService:
    """Provides a TradeService instance with mocked dependencies."""
    return TradeService(repo=mock_repo, notifier=notifier)

def test_create_and_publish_happy_path(trade_service: TradeService, mock_repo: MagicMock, mock_notifier: MagicMock):
    """
    Tests the successful creation flow:
    1. Card is posted to Telegram.
    2. Recommendation is saved to the database.
    3. Card is edited with the new ID.
    """
    # Arrange
    # Mock the return value of the notifier when it posts the initial card
    mock_notifier.post_recommendation_card.return_value = (12345, 67890)  # (channel_id, message_id)
    
    # Mock the return value of the repository after saving
    # The saved object must have an `id` attribute for the final edit step
    saved_rec_mock = Recommendation(
        id=99, asset=Symbol("BTCUSDT"), side=Side("LONG"), entry=Price(50000),
        stop_loss=Price(49000), targets=Targets([51000]), channel_id=12345, message_id=67890
    )
    mock_repo.add.return_value = saved_rec_mock

    # Act
    result = trade_service.create_and_publish_recommendation(
        asset="BTCUSDT", side="LONG", market="Futures", entry=50000,
        stop_loss=49000, targets=[51000], notes="Test", user_id="test_user"
    )

    # Assert
    # 1. Verify that the notifier was called to post the initial card
    mock_notifier.post_recommendation_card.assert_called_once()
    
    # 2. Verify that the repository was called to save the recommendation
    mock_repo.add.assert_called_once()
    
    # 3. Verify that the notifier was called AGAIN to edit the card with the final ID
    mock_notifier.edit_recommendation_card.assert_called_once_with(saved_rec_mock)
    
    # 4. Check the final result
    assert result.id == 99
    assert result.channel_id == 12345

def test_create_fails_if_telegram_post_fails(trade_service: TradeService, mock_repo: MagicMock, mock_notifier: MagicMock):
    """
    Tests that if posting to Telegram fails, the recommendation is NOT saved to the DB.
    """
    # Arrange
    mock_notifier.post_recommendation_card.return_value = None

    # Act & Assert
    with pytest.raises(RuntimeError, match="Could not publish to Telegram"):
        trade_service.create_and_publish_recommendation(
            asset="ETHUSDT", side="SHORT", market="Spot", entry=3000,
            stop_loss=3100, targets=[2900], notes=None, user_id="test_user"
        )
    
    # Ensure that the database was never touched
    mock_repo.add.assert_not_called()

def test_close_recommendation_flow(trade_service: TradeService, mock_repo: MagicMock, mock_notifier: MagicMock):
    """
    Tests the successful closing flow:
    1. Gets the recommendation from the repo.
    2. Updates its state.
    3. Saves the update to the repo.
    4. Edits the Telegram card to show it's closed.
    """
    # Arrange
    rec_id = 101
    exit_price = 50500.0
    
    # Mock the recommendation object that the repo will return
    open_rec = Recommendation(
        id=rec_id, asset=Symbol("BTCUSDT"), side=Side("LONG"), entry=Price(50000),
        stop_loss=Price(49000), targets=Targets([51000]), status="OPEN"
    )
    mock_repo.get.return_value = open_rec
    
    # The `repo.update` method should return the updated object
    mock_repo.update.return_value = open_rec 

    # Act
    result = trade_service.close(rec_id, exit_price)

    # Assert
    # 1. Verify it fetched the correct recommendation
    mock_repo.get.assert_called_once_with(rec_id)
    
    # 2. Check that the object's state was changed correctly
    assert result.status == "CLOSED"
    assert result.exit_price == exit_price
    assert isinstance(result.closed_at, datetime)
    
    # 3. Verify it saved the changes
    mock_repo.update.assert_called_once_with(open_rec)
    
    # 4. Verify it notified the channel by editing the card
    mock_notifier.edit_recommendation_card.assert_called_once_with(open_rec)

def test_close_non_existent_recommendation_raises_error(trade_service: TradeService, mock_repo: MagicMock):
    """
    Tests that closing a recommendation that doesn't exist raises a ValueError.
    """
    # Arrange
    rec_id = 404
    mock_repo.get.return_value = None

    # Act & Assert
    with pytest.raises(ValueError, match="Recommendation 404 not found"):
        trade_service.close(rec_id, 3000.0)

# --- END OF FILE ---