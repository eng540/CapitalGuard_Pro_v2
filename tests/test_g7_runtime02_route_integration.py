from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from capitalguard.application.services.creation_service import CreationService
from capitalguard.application.services.publication_outbox_service import PublicationOutboxService
from capitalguard.domain.entities import UserType as UserTypeEntity
from capitalguard.infrastructure.db.models import (
    PublicationDelivery,
    Recommendation,
    WebCommandAudit,
)
from capitalguard.infrastructure.db.repository import RecommendationRepository, UserRepository
from capitalguard.interfaces.api.routers import webapp


pytestmark = pytest.mark.asyncio


def _creation_service():
    notifier = MagicMock()
    outbox = PublicationOutboxService(RecommendationRepository(), notifier)
    return CreationService(
        RecommendationRepository(),
        notifier,
        MagicMock(),
        MagicMock(get_cached_price=AsyncMock(return_value=77000)),
        outbox_service=outbox,
    )


def _request(creation_service):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(services={"creation_service": creation_service}),
        ),
    )


def _legacy_payload(key):
    return webapp.LegacyWebAppSignal(
        initData=None,
        actor_telegram_id=874200,
        asset="BTCUSDT",
        side="LONG",
        market="Futures",
        order_type="LIMIT",
        entry=77000,
        stop_loss=76000,
        targets_raw="78000",
        notes="same canonical payload",
        leverage="5",
        channel_ids=[],
        idempotency_key=key,
    )


def _confirm_payload(key):
    return webapp.AnalystRecommendationConfirm(
        initData=None,
        actor_telegram_id=874200,
        asset="BTCUSDT",
        side="LONG",
        market="Futures",
        order_type="LIMIT",
        entry=77000,
        stop_loss=76000,
        targets_raw="78000",
        notes="same canonical payload",
        leverage="5",
        channel_ids=[],
        idempotency_key=key,
    )


async def test_legacy_and_canonical_routes_share_one_command_and_one_persistent_effect(
    db_session,
    monkeypatch,
):
    analyst = UserRepository(db_session).find_or_create(
        telegram_id=874200,
        first_name="Legacy Compatibility Analyst",
        user_type=UserTypeEntity.ANALYST,
        is_active=True,
    )
    db_session.flush()
    creation_service = _creation_service()
    request = _request(creation_service)
    command_key = "shared-web-command-0001"

    @contextmanager
    def session_scope_for_test():
        yield db_session

    monkeypatch.setattr(webapp, "session_scope", session_scope_for_test)
    monkeypatch.setattr(
        webapp,
        "resolve_webapp_actor",
        lambda payload, request: analyst.telegram_user_id,
    )

    legacy_response = SimpleNamespace(headers={})
    legacy_result = await webapp.create_trade_webapp(
        _legacy_payload(command_key),
        request,
        legacy_response,
    )
    canonical_result = await webapp.confirm_recommendation_webapp(
        _confirm_payload(command_key),
        request,
    )

    assert legacy_result["ok"] is True
    assert canonical_result == legacy_result
    assert legacy_response.headers["Deprecation"] == "true"
    assert db_session.query(Recommendation).count() == 1
    assert db_session.query(PublicationDelivery).count() == 0
    assert db_session.query(WebCommandAudit).count() == 1


async def test_legacy_route_rejects_same_key_with_different_payload(monkeypatch):
    command = webapp.WebCommandService
    first = command.derive_compatibility_idempotency_key(
        874201,
        {
            "asset": "BTCUSDT",
            "side": "LONG",
            "market": "Futures",
            "order_type": "LIMIT",
            "entry": Decimal("77000"),
            "stop_loss": Decimal("76000"),
            "targets": [{"price": Decimal("78000"), "close_percent": 0.0}],
            "notes": "x",
            "target_channel_ids": set(),
        },
    )
    second = command.derive_compatibility_idempotency_key(
        874201,
        {
            "asset": "BTCUSDT",
            "side": "LONG",
            "market": "Futures",
            "order_type": "LIMIT",
            "entry": Decimal("77100"),
            "stop_loss": Decimal("76000"),
            "targets": [{"price": Decimal("78000"), "close_percent": 0.0}],
            "notes": "x",
            "target_channel_ids": set(),
        },
    )

    assert first.startswith("legacy-create-")
    assert first != second
