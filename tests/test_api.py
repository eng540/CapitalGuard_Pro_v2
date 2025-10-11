import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

# Set environment variables for a predictable test environment
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["API_KEY"] = "test_api_key"
os.environ["TELEGRAM_BOT_TOKEN"] = "fake_token_for_tests"
os.environ["ADMIN_USERNAMES"] = "test_admin"

# Must import the app after setting env vars
from capitalguard.interfaces.api.main import app

@pytest.fixture(scope="function")
def client() -> TestClient:
    """
    Provides a TestClient for the API. This fixture ensures that the app's
    lifespan events (startup/shutdown) are correctly handled for each test function,
    and that the database is reset.
    """
    if os.path.exists("./test.db"):
        os.remove("./test.db")

    # We patch the bootstrap process to avoid real Telegram bot initialization
    # and instead inject our mock services.
    with patch("capitalguard.interfaces.api.main.bootstrap_app", return_value=AsyncMock()) as mock_bootstrap:
        with patch("capitalguard.interfaces.api.main.build_services", return_value={}) as mock_build:
            # The 'with' statement triggers the lifespan events.
            with TestClient(app) as test_client:
                yield test_client

    if os.path.exists("./test.db"):
        os.remove("./test.db")


HEADERS = {"X-API-Key": "test_api_key"}

def test_root_endpoint(client: TestClient):
    """Tests the root endpoint, which requires no auth."""
    response = client.get("/")
    assert response.status_code == 200
    assert "CapitalGuard API" in response.json()["message"]

def test_health_check_endpoint(client: TestClient):
    """Tests the /health endpoint, which requires no auth."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

# This test is skipped because the /recommendations endpoint does not exist in the API.
# The primary interface for creating recommendations is the Telegram bot.
# This test can be enabled if a REST API for recommendations is built in the future.
@pytest.mark.skip(reason="No /recommendations endpoint in the current API design.")
def test_api_key_protection(client: TestClient):
    """Tests that a hypothetical future endpoint would be protected by the API key."""
    response_wrong_key = client.get("/recommendations", headers={"X-API-Key": "wrong_key"})
    assert response_wrong_key.status_code == 401

    response_no_key = client.get("/recommendations")
    assert response_no_key.status_code == 401

# This test is also skipped for the same reason. It demonstrates how one might test
# a fully integrated API flow, but it's not applicable to the current system.
@pytest.mark.skip(reason="No recommendation management endpoints in the current API design.")
def test_list_and_close_recommendation_flow(client: TestClient):
    """
    A hypothetical test for a future API that can create, list, and close recommendations.
    """
    # In a real scenario, you would first POST to create a recommendation
    # For now, this test remains as a template.
    pass