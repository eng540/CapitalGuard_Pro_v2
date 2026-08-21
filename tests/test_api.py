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


def test_v1_status_is_versioned_nonfinancial_and_fail_closed(client: TestClient):
    app.state.ready = False
    app.state.ptb_app = None
    app.state.services = None

    not_ready = client.get("/api/v1/status")
    assert not_ready.status_code == 503
    assert not_ready.json()["detail"] == "Service is not ready"

    app.state.ready = True
    app.state.ptb_app = object()
    app.state.services = {"test_service": object()}
    ready = client.get("/api/v1/status")

    assert ready.status_code == 200
    assert ready.json() == {
        "api_version": "v1",
        "service": "capitalguard-core",
        "status": "ok",
        "commercial_mode": "noncommercial",
    }


def test_webapp_rejects_invalid_telegram_init_data(client: TestClient):
    response = client.get("/api/webapp/portfolio", params={"initData": "invalid"})

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "error" in response.json()


def test_core_read_model_requires_a_server_service_key(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from capitalguard.config import settings

    monkeypatch.setattr(settings, "API_KEY", "test-service-key")
    path = "/api/webapp/read-models/trader/123456"

    missing = client.get(path)
    rejected = client.get(path, headers={"Authorization": "Bearer wrong-key"})

    assert missing.status_code == 401
    assert rejected.status_code == 403


def test_owner_command_surface_requires_the_core_service_key(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from capitalguard.config import settings

    monkeypatch.setattr(settings, "API_KEY", "test-service-key")
    response = client.get("/api/webapp/owner/review-batches", params={"actor_telegram_id": 123456})

    assert response.status_code == 401


def test_operations_feed_requires_the_core_service_key(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from capitalguard.config import settings

    monkeypatch.setattr(settings, "API_KEY", "test-service-key")
    response = client.get("/api/webapp/owner/operations-feed", params={"actor_telegram_id": 123456})

    assert response.status_code == 401


def test_additional_read_models_require_the_core_service_key(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from capitalguard.config import settings

    monkeypatch.setattr(settings, "API_KEY", "test-service-key")

    assert client.get("/api/webapp/read-models/analysts").status_code == 401
    assert client.get("/api/webapp/read-models/trader/123456/recommendations").status_code == 401
    assert client.get("/api/webapp/read-models/trader/123456/recommendations/USR-000001/T-0001").status_code == 401
    assert client.get("/api/webapp/read-models/trader/123456/historical").status_code == 401


def test_r5_readiness_requires_the_core_service_key(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from capitalguard.config import settings

    monkeypatch.setattr(settings, "API_KEY", "test-service-key")

    assert client.get("/api/webapp/owner/r5-readiness?actor_telegram_id=123456").status_code == 401


def test_removed_legacy_recommendations_surface_is_not_accidentally_reintroduced(client: TestClient):
    response = client.get("/recommendations")
    assert response.status_code == 404
