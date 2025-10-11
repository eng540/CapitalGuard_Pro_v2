# --- START OF FILE: tests/test_api.py ---
import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

# Set environment variables before importing the app
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["API_KEY"] = "test_api_key"
os.environ["TELEGRAM_BOT_TOKEN"] = "fake_token" # Required for app setup
os.environ["TELEGRAM_CHAT_ID"] = "12345"

from capitalguard.interfaces.api.main import app

@pytest.fixture
def client() -> TestClient:
    """Provides a TestClient for the API."""
    # Ensure the test database is clean for each test
    if os.path.exists("./test.db"):
        os.remove("./test.db")
    # We use a patch to mock the notifier to avoid real Telegram calls during tests
    with patch("capitalguard.boot.TelegramNotifier") as MockNotifier:
        # Configure the mock to simulate a successful post
        instance = MockNotifier.return_value
        instance.post_recommendation_card.return_value = (12345, 67890)
        instance.edit_recommendation_card.return_value = True
        yield TestClient(app)
    # Cleanup after tests
    if os.path.exists("./test.db"):
        os.remove("./test.db")


HEADERS = {"X-API-Key": "test_api_key"}

def test_root_endpoint(client: TestClient):
    """Tests the root endpoint."""
    r = client.get("/")
    assert r.status_code == 200
    assert "CapitalGuard API" in r.json()["message"]

def test_api_key_protection(client: TestClient):
    """Tests that endpoints are protected by the API key."""
    assert client.get("/recommendations", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/recommendations").status_code == 401 # No key provided

# Note: Testing the POST /recommendations endpoint directly is complex because it's part of the Telegram conversation flow.
# It's better to test the service logic directly, as we did in test_services.py.
# Here, we will test the GET and POST-close endpoints which are pure API.

def test_list_and_close_recommendation_flow(client: TestClient):
    """
    This test simulates a more realistic API flow by first creating a recommendation
    via the service layer (as if the bot did it), then managing it via the API.
    """
    # Arrange: We need to inject a recommendation into the database first.
    # We do this by accessing the services created by the app.
    trade_service = app.state.services["trade_service"]
    created_rec = trade_service.create_and_publish_recommendation(
        asset="ETHUSDT", side="SHORT", market="Futures", entry=3000,
        stop_loss=3100, targets=[2900, 2800], notes="API test", user_id="api_user"
    )
    rec_id = created_rec.id

    # Act 1: List recommendations and check if our new one is there
    r_list = client.get("/recommendations", headers=HEADERS)
    assert r_list.status_code == 200
    recs_list = r_list.json()
    found = any(item["id"] == rec_id and item["status"] == "OPEN" for item in recs_list)
    assert found, "The newly created recommendation was not found in the list."

    # Act 2: Close the recommendation via the API endpoint
    r_close = client.post(f"/recommendations/{rec_id}/close", json={"exit_price": 2950.0}, headers=HEADERS)
    
    # Assert 2
    assert r_close.status_code == 200, f"API call failed with: {r_close.text}"
    closed_rec_data = r_close.json()
    assert closed_rec_data["status"] == "CLOSED"
    assert closed_rec_data["exit_price"] == 2950.0

    # Act 3: List again to confirm the status change
    r_list_after = client.get("/recommendations", headers=HEADERS)
    found_closed = any(item["id"] == rec_id and item["status"] == "CLOSED" for item in r_list_after.json())
    assert found_closed, "The recommendation was not found with a CLOSED status after closing."

# --- END OF FILE ---