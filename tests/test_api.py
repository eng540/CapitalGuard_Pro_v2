import os

import pytest
from fastapi.testclient import TestClient

# Keep import-time configuration deterministic. Startup is intentionally not
# entered in these unit smoke tests because Redis/Telegram are external deps.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:fake_token")
os.environ.setdefault("ENV", "test")

from capitalguard.interfaces.api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_root_endpoint(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "CapitalGuard API" in response.json()["message"]


def test_health_is_fail_closed_before_startup(client: TestClient):
    app.state.ready = False
    app.state.ptb_app = None
    app.state.services = None

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["detail"] == "Service is not ready"


def test_webapp_rejects_invalid_telegram_init_data(client: TestClient):
    response = client.get("/api/webapp/portfolio", params={"initData": "invalid"})

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "error" in response.json()


def test_removed_legacy_recommendations_surface_is_not_accidentally_reintroduced(client: TestClient):
    response = client.get("/recommendations")
    assert response.status_code == 404
