import pytest
from unittest.mock import MagicMock
from types import SimpleNamespace

from capitalguard.application.services.trade_service import TradeService

@pytest.fixture
def mock_repo() -> MagicMock:
    return MagicMock()

@pytest.fixture
def mock_notifier() -> MagicMock:
    return MagicMock()

def test_trade_service_create_calls_repo_and_notifier(mock_repo: MagicMock, mock_notifier: MagicMock):
    # repo.add returns an object that looks like a Recommendation (only id is relevant here)
    mock_repo.add.return_value = SimpleNamespace(id=123)

    service = TradeService(mock_repo, mock_notifier)

    # Using a string interface keeps this test decoupled from domain enums/classes
    result = service.create("BTCUSDT", "LONG", 65000.0, 63000.0, [66000.0])

    mock_repo.add.assert_called_once()
    mock_notifier.publish.assert_called_once()
    assert getattr(result, "id", None) == 123

def test_trade_service_close_updates_and_notifies(mock_repo: MagicMock, mock_notifier: MagicMock):
    rec_id = 456
    exit_price = 66000.0

    # Minimal object to be mutated by service.close()
    recommendation = SimpleNamespace(id=rec_id, status="OPEN", exit_price=None)
    mock_repo.get.return_value = recommendation

    service = TradeService(mock_repo, mock_notifier)

    service.close(rec_id, exit_price)

    mock_repo.get.assert_called_once_with(rec_id)
    assert recommendation.status == "CLOSED"
    assert recommendation.exit_price == exit_price
    mock_repo.update.assert_called_once_with(recommendation)
    mock_notifier.publish.assert_called_once()