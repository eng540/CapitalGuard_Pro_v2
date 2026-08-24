from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from capitalguard.interfaces.api.routers import webapp


pytestmark = pytest.mark.asyncio


class _SessionScope:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc_value, traceback):
        return False


def _request():
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                services={"creation_service": object()},
            ),
        ),
    )


def _payload(**overrides):
    values = {
        "asset": "BTCUSDT",
        "side": "LONG",
        "market": "Futures",
        "order_type": "LIMIT",
        "entry": 77000,
        "stop_loss": 76000,
        "targets_raw": "78000",
        "notes": "legacy compatibility",
        "leverage": "5",
        "channel_ids": [],
        "initData": None,
        "actor_telegram_id": 874100,
    }
    values.update(overrides)
    return webapp.LegacyWebAppSignal(**values)


async def test_legacy_create_is_thin_adapter_to_canonical_command(monkeypatch):
    captured = {}

    async def fake_confirm(self, session, **kwargs):
        captured.update(kwargs)
        return {"ok": True, "public_ref": "REC-1", "replayed": False}

    monkeypatch.setattr(webapp, "resolve_webapp_actor", lambda payload, request: 874100)
    monkeypatch.setattr(
        webapp.WebCommandService,
        "confirm_analyst_recommendation",
        fake_confirm,
    )
    monkeypatch.setattr(webapp, "session_scope", lambda: _SessionScope())

    response = SimpleNamespace(headers={})
    result = await webapp.create_trade_webapp(_payload(), _request(), response)

    assert result == {"ok": True, "public_ref": "REC-1", "replayed": False}
    assert response.headers["Deprecation"] == "true"
    assert "/api/webapp/recommendations/confirm" in response.headers["Link"]
    assert captured["actor_telegram_id"] == 874100
    assert captured["recommendation"]["asset"] == "BTCUSDT"
    assert captured["recommendation"]["targets"]
    assert captured["idempotency_key"].startswith("legacy-create-")


async def test_legacy_explicit_idempotency_key_is_forwarded_unchanged(monkeypatch):
    captured = {}

    async def fake_confirm(self, session, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(webapp, "resolve_webapp_actor", lambda payload, request: 874101)
    monkeypatch.setattr(
        webapp.WebCommandService,
        "confirm_analyst_recommendation",
        fake_confirm,
    )
    monkeypatch.setattr(webapp, "session_scope", lambda: _SessionScope())

    payload = _payload(actor_telegram_id=874101, idempotency_key="legacy-command-0001")
    await webapp.create_trade_webapp(payload, _request(), SimpleNamespace(headers={}))

    assert captured["idempotency_key"] == "legacy-command-0001"


async def test_legacy_adapter_does_not_call_creation_service_directly(monkeypatch):
    direct_creation = AsyncMock(side_effect=AssertionError("direct CreationService bypass"))
    request = _request()
    request.app.state.services["creation_service"] = SimpleNamespace(
        create_and_publish_recommendation_async=direct_creation,
    )

    async def fake_confirm(self, session, **kwargs):
        return {"ok": True}

    monkeypatch.setattr(webapp, "resolve_webapp_actor", lambda payload, request: 874102)
    monkeypatch.setattr(
        webapp.WebCommandService,
        "confirm_analyst_recommendation",
        fake_confirm,
    )
    monkeypatch.setattr(webapp, "session_scope", lambda: _SessionScope())

    result = await webapp.create_trade_webapp(
        _payload(actor_telegram_id=874102),
        request,
        SimpleNamespace(headers={}),
    )

    assert result == {"ok": True}
    direct_creation.assert_not_awaited()
