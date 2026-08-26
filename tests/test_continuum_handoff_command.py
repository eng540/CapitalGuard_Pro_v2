from sqlalchemy import select

from capitalguard.application.services.historical_signal_materialization_service import HistoricalSignalMaterializationService
from capitalguard.application.services.web_command_service import WebCommandService
from capitalguard.config import settings
from capitalguard.domain.entities import UserType
from capitalguard.infrastructure.db.models import Recommendation, UserTrade, WebCommandAudit
from capitalguard.infrastructure.db.repository import UserRepository
from tests.test_historical_signal_materialization_service import accepted_g5_draft


def test_continuum_command_records_blocked_decision_idempotently_without_live_side_effects(db_session, monkeypatch):
    actor_telegram_id = 99201
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", str(actor_telegram_id))
    UserRepository(db_session).find_or_create(
        telegram_id=actor_telegram_id,
        user_type=UserType.ANALYST,
        first_name="Continuum Owner",
    )
    draft, _revision = accepted_g5_draft(db_session)
    signal = HistoricalSignalMaterializationService().materialize(db_session, draft_id=draft.id)
    service = WebCommandService()

    first = service.assess_continuum_handoff(
        db_session,
        actor_telegram_id=actor_telegram_id,
        signal_id=signal.id,
        consent_given=True,
        idempotency_key="continuum-decision-001",
    )
    second = service.assess_continuum_handoff(
        db_session,
        actor_telegram_id=actor_telegram_id,
        signal_id=signal.id,
        consent_given=True,
        idempotency_key="continuum-decision-001",
    )

    assert first == second
    assert first["status"] == "HANDOFF_BLOCKED"
    assert first["approved"] is False
    assert first["execution_allowed"] is False
    assert "REPLAY_EVIDENCE_UNVERIFIED" in first["reason_codes"]
    assert "PROTECTION_POLICY_INVALID_OR_MISSING" in first["reason_codes"]
    assert first["replayed"] is False
    assert len(db_session.execute(select(WebCommandAudit).where(WebCommandAudit.idempotency_key == "continuum-decision-001")).scalars().all()) == 1
    assert db_session.execute(select(Recommendation)).scalars().all() == []
    assert db_session.execute(select(UserTrade)).scalars().all() == []


def test_continuum_command_rejects_idempotency_reuse_with_different_consent(db_session, monkeypatch):
    actor_telegram_id = 99202
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", str(actor_telegram_id))
    UserRepository(db_session).find_or_create(
        telegram_id=actor_telegram_id,
        user_type=UserType.ANALYST,
        first_name="Continuum Owner Two",
    )
    draft, _revision = accepted_g5_draft(db_session)
    signal = HistoricalSignalMaterializationService().materialize(db_session, draft_id=draft.id)
    service = WebCommandService()
    service.assess_continuum_handoff(
        db_session,
        actor_telegram_id=actor_telegram_id,
        signal_id=signal.id,
        consent_given=False,
        idempotency_key="continuum-decision-002",
    )

    import pytest
    from capitalguard.application.services.web_command_service import WebCommandError

    with pytest.raises(WebCommandError, match="cannot be reused"):
        service.assess_continuum_handoff(
            db_session,
            actor_telegram_id=actor_telegram_id,
            signal_id=signal.id,
            consent_given=True,
            idempotency_key="continuum-decision-002",
        )


import pytest

from capitalguard.application.services.continuum_handoff_gate import ContinuumHandoffDecision, HandoffStatus


class _FakeCreationService:
    def __init__(self):
        self.calls = []

    async def create_trade_from_forwarding_async(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "success": True,
            "trade_id": 704,
            "public_ref": "TRD-CONTINUUM-704",
            "asset": kwargs["trade_data"]["asset"],
        }


def test_execute_continuum_handoff_reuses_creation_contract_as_pending_only(db_session, monkeypatch):
    actor_telegram_id = 99203
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", str(actor_telegram_id))
    UserRepository(db_session).find_or_create(
        telegram_id=actor_telegram_id,
        user_type=UserType.ANALYST,
        first_name="Continuum Executor",
    )
    draft, _revision = accepted_g5_draft(db_session)
    signal = HistoricalSignalMaterializationService().materialize(db_session, draft_id=draft.id)
    signal.status = "ACTIVE"
    signal.evidence.ownership_proof_ref = "test://continuum/owner"
    signal.evidence.batch.status = "EVIDENCE_INGESTED"
    signal.evidence.metadata_json = {
        "continuum_duplicate_exists": False,
        "continuum_lifecycle_status": "ACTIVE",
        "continuum_protection_policy": {
            "profit_stop_mode": "TRAILING",
            "profit_stop_active": True,
            "side": "LONG",
            "entry": "69000",
            "stop_loss": "68000",
            "profit_stop_trailing_value": "500",
        },
    }
    db_session.flush()

    monkeypatch.setattr(
        "capitalguard.application.services.web_command_service.ContinuumHandoffGate.evaluate",
        lambda _self, _facts: ContinuumHandoffDecision(
            status=HandoffStatus.APPROVED,
            reason_codes=(),
            approved=True,
        ),
    )
    creation = _FakeCreationService()
    result = __import__("asyncio").run(
        WebCommandService().execute_continuum_handoff(
            db_session,
            actor_telegram_id=actor_telegram_id,
            signal_id=signal.id,
            consent_given=True,
            idempotency_key="continuum-execute-001",
            creation_service=creation,
        )
    )

    assert result["status"] == "PENDING_ACTIVATION"
    assert result["approved"] is True
    assert result["execution_allowed"] is False
    assert result["live_activation"] is False
    assert len(creation.calls) == 1
    assert creation.calls[0]["status_to_set"] == "PENDING_ACTIVATION"
    assert creation.calls[0]["source_type"] == "CONTINUUM_HANDOFF"
    assert creation.calls[0]["original_published_at"] == signal.evidence.message_timestamp
    assert signal.evidence.metadata_json["continuum_handoff_status"] == "PENDING_ACTIVATION"
    assert db_session.execute(select(Recommendation)).scalars().all() == []
