from sqlalchemy import select

from capitalguard.application.services.entitlement_service import BillingDisabledError, EntitlementService
from capitalguard.domain.entities import UserType
from capitalguard.infrastructure.db.models import SubscriptionLedgerEntry
from capitalguard.infrastructure.db.repository import UserRepository


def _user(db_session, telegram_id):
    return UserRepository(db_session).find_or_create(
        telegram_id=telegram_id,
        user_type=UserType.TRADER,
        first_name="Alpha Trader",
    )


def test_alpha_grant_is_zero_value_and_idempotent(db_session):
    user = _user(db_session, 5101)
    service = EntitlementService(billing_enabled=False)

    first = service.grant_alpha(
        db_session,
        user.id,
        ["ANALYST_DISCOVERY", "ANALYST_DISCOVERY"],
        idempotency_key="alpha-user-5101",
    )
    second = service.grant_alpha(
        db_session,
        user.id,
        ["ANALYST_DISCOVERY"],
        idempotency_key="alpha-user-5101",
    )
    db_session.flush()

    assert len(first) == 1
    assert first[0].id == second[0].id
    assert service.has_feature(db_session, user.id, "analyst_discovery") is True
    assert service.list_active(db_session, user.id) == ["ANALYST_DISCOVERY"]

    ledger_rows = db_session.execute(select(SubscriptionLedgerEntry)).scalars().all()
    assert len(ledger_rows) == 1
    assert ledger_rows[0].amount_minor == 0
    assert ledger_rows[0].provider == "INTERNAL"


def test_revoke_is_append_only_and_removes_effective_access(db_session):
    user = _user(db_session, 5102)
    service = EntitlementService(billing_enabled=False)
    service.grant_alpha(db_session, user.id, ["WEB_PORTFOLIO"], idempotency_key="alpha-user-5102")
    service.revoke(db_session, user.id, "WEB_PORTFOLIO", idempotency_key="revoke-user-5102")
    db_session.flush()

    assert service.has_feature(db_session, user.id, "WEB_PORTFOLIO") is False
    assert service.list_active(db_session, user.id) == []


def test_commercial_gate_rejects_service_when_explicitly_enabled(db_session):
    user = _user(db_session, 5103)
    service = EntitlementService(billing_enabled=True)

    try:
        service.grant_alpha(db_session, user.id, ["ANALYST_DISCOVERY"], idempotency_key="alpha-user-5103")
    except BillingDisabledError:
        pass
    else:
        raise AssertionError("commercial gate must reject the R3 non-commercial service")
