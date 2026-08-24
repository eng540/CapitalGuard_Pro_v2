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


def test_metrics_record_safe_route_templates_and_exclude_metrics_scrapes(client: TestClient):
    app.state.ready = False
    app.state.ptb_app = None
    app.state.services = None

    assert client.get("/health").status_code == 503
    metrics = client.get("/metrics")

    assert metrics.status_code == 200
    assert 'cg_http_requests_total{method="GET",route="/health",status="503"}' in metrics.text
    assert 'cg_http_request_latency_seconds_count{method="GET",route="/health",status="503"}' in metrics.text
    assert 'route="/metrics"' not in metrics.text


def test_v1_status_is_versioned_nonfinancial_and_fail_closed(client: TestClient):
    from capitalguard.interfaces.api.routers.v1 import reset_status_rate_limiter

    reset_status_rate_limiter()
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


def test_v1_status_rate_limit_is_scoped_to_public_metadata(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from capitalguard.interfaces.api.routers import v1

    monkeypatch.setattr(v1, "STATUS_RATE_LIMIT_PER_MINUTE", 2)
    v1.reset_status_rate_limiter()
    app.state.ready = True
    app.state.ptb_app = object()
    app.state.services = {"test_service": object()}

    assert client.get("/api/v1/status").status_code == 200
    assert client.get("/api/v1/status").status_code == 200
    limited = client.get("/api/v1/status")

    assert limited.status_code == 429
    assert limited.headers["retry-after"]
    assert limited.json()["detail"] == "Public status rate limit exceeded"


def test_webapp_rejects_invalid_telegram_init_data(client: TestClient):
    response = client.get("/api/webapp/portfolio", params={"initData": "invalid"})

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "error" in response.json()


def test_webapp_verifies_telegram_session_through_an_authenticated_post(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from capitalguard.config import settings
    from capitalguard.interfaces.api.routers import webapp

    monkeypatch.setattr(settings, "API_KEY", "core-service-test-key")
    monkeypatch.setattr(webapp, "validate_telegram_data", lambda init_data, bot_token: {"id": 123456})

    rejected = client.post("/api/webapp/telegram/verify", json={"init_data": "telegram-proof"})
    accepted = client.post(
        "/api/webapp/telegram/verify",
        headers={"Authorization": "Bearer core-service-test-key"},
        json={"init_data": "telegram-proof"},
    )

    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json() == {"ok": True, "telegram_id": 123456}


def test_telegram_webhook_rejects_missing_or_invalid_secret(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from capitalguard.config import settings

    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "tg-webhook-test-secret")

    missing = client.post("/webhook/telegram", json={})
    invalid = client.post(
        "/webhook/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
        json={},
    )

    assert missing.status_code == 403
    assert invalid.status_code == 403


def test_telegram_webhook_accepts_matching_secret_before_processing(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from capitalguard.config import settings

    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "tg-webhook-test-secret")
    app.state.ptb_app = None

    response = client.post(
        "/webhook/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "tg-webhook-test-secret"},
        json={},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_webhook_secret_validation_rejects_telegram_unallowed_characters():
    from capitalguard.interfaces.api.main import validate_telegram_webhook_secret

    with pytest.raises(RuntimeError, match="only A-Z"):
        validate_telegram_webhook_secret("invalid secret@value")


def test_webhook_secret_validation_accepts_telegram_character_contract():
    from capitalguard.interfaces.api.main import validate_telegram_webhook_secret

    assert validate_telegram_webhook_secret("tg_secret-2026_VALID") == "tg_secret-2026_VALID"


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


def test_manual_historical_binance_replay_is_retired(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from capitalguard.config import settings

    monkeypatch.setattr(settings, "API_KEY", "test-service-key")
    response = client.post("/api/webapp/owner/historical-signals/9/replay-binance", json={"actor_telegram_id": 123456, "signal_id": 9, "start": "2026-01-01T00:00:00Z", "end": "2026-01-01T01:00:00Z", "idempotency_key": "historical-replay-key-0001"})

    assert response.status_code == 410


def test_reviewed_batch_binance_replay_requires_the_core_service_key(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from capitalguard.config import settings

    monkeypatch.setattr(settings, "API_KEY", "test-service-key")
    response = client.post("/api/webapp/owner/review-batches/9/replay-binance", json={"actor_telegram_id": 123456, "batch_id": 9, "idempotency_key": "historical-batch-replay-key-0001"})

    assert response.status_code == 401


def test_reviewed_batch_binance_replay_rejects_path_body_mismatch(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from capitalguard.config import settings

    monkeypatch.setattr(settings, "API_KEY", "test-service-key")
    response = client.post(
        "/api/webapp/owner/review-batches/9/replay-binance",
        headers={"Authorization": "Bearer test-service-key"},
        json={"actor_telegram_id": 123456, "batch_id": 10, "idempotency_key": "historical-batch-replay-key-0001"},
    )

    assert response.status_code == 422


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


def test_analyst_publication_status_requires_the_core_service_key(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from capitalguard.config import settings

    monkeypatch.setattr(settings, "API_KEY", "test-service-key")
    response = client.get(
        "/api/webapp/recommendations/AN-000001%2FR-0001/publication",
        params={"actor_telegram_id": 123456},
    )

    assert response.status_code == 401


def test_legacy_numeric_trade_action_is_retired(client: TestClient):
    response = client.post("/api/webapp/action")

    assert response.status_code == 410
    assert response.json()["detail"] == "Legacy trade action endpoint is retired"


def test_user_trade_command_rejects_actor_outside_trader_scope(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from capitalguard.config import settings

    monkeypatch.setattr(settings, "API_KEY", "test-service-key")
    response = client.post(
        "/api/webapp/read-models/trader/123456/recommendations/USR-000001%2FT-0001/commands/close",
        headers={"Authorization": "Bearer test-service-key"},
        json={"actor_telegram_id": 999999, "idempotency_key": "tg04-command-key"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Command actor does not match the trader scope"


def test_r5_readiness_requires_the_core_service_key(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from capitalguard.config import settings

    monkeypatch.setattr(settings, "API_KEY", "test-service-key")

    assert client.get("/api/webapp/owner/r5-readiness?actor_telegram_id=123456").status_code == 401


def test_historical_quality_requires_the_core_service_key(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from capitalguard.config import settings

    monkeypatch.setattr(settings, "API_KEY", "test-service-key")
    response = client.get("/api/webapp/owner/historical-quality", params={"actor_telegram_id": 123456})

    assert response.status_code == 401


def test_removed_legacy_recommendations_surface_is_not_accidentally_reintroduced(client: TestClient):
    response = client.get("/recommendations")
    assert response.status_code == 404


def test_historical_intake_surface_requires_core_service_key(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from capitalguard.config import settings

    monkeypatch.setattr(settings, "API_KEY", "test-service-key")
    payload = {
        "actor_telegram_id": 123456,
        "source_kind": "MANUAL_ADMIN_IMPORT",
        "input_mode": "PASTE",
        "items": [{"item_key": "one", "raw_text": "#BTCUSDT LONG Entry 100 SL 95 TP1 105"}],
    }
    create = client.post("/api/webapp/historical/intake", json=payload)
    listing = client.get("/api/webapp/historical/intake", params={"actor_telegram_id": 123456})
    detail = client.get("/api/webapp/historical/intake/1", params={"actor_telegram_id": 123456})

    assert create.status_code == 401
    assert listing.status_code == 401
    assert detail.status_code == 401


def test_historical_intake_rejects_empty_items_after_service_auth(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from capitalguard.config import settings

    monkeypatch.setattr(settings, "API_KEY", "test-service-key")
    response = client.post(
        "/api/webapp/historical/intake",
        headers={"Authorization": "Bearer test-service-key"},
        json={"actor_telegram_id": 123456, "source_kind": "MANUAL_ADMIN_IMPORT", "input_mode": "PASTE", "items": []},
    )

    assert response.status_code == 422
    assert "between 1 and 5000" in response.json()["detail"]
