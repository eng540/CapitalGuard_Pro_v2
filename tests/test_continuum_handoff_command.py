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
