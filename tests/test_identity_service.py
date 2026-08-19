from capitalguard.application.services.identity_service import IdentityService
from capitalguard.domain.entities import UserType
from capitalguard.infrastructure.db.repository import UserRepository


def test_public_ref_is_opaque_and_typed():
    ref = IdentityService.public_ref("rec")

    assert ref.startswith("REC-")
    assert len(ref) == 30
    assert ref != IdentityService.public_ref("rec")


def test_scoped_sequences_are_independent_by_owner(db_session):
    repository = UserRepository(db_session)
    analyst_one = repository.find_or_create(telegram_id=1001, user_type=UserType.ANALYST)
    analyst_two = repository.find_or_create(telegram_id=1002, user_type=UserType.ANALYST)
    trader_one = repository.find_or_create(telegram_id=2001, user_type=UserType.TRADER)
    trader_two = repository.find_or_create(telegram_id=2002, user_type=UserType.TRADER)

    first_one = IdentityService.recommendation_identity(db_session, analyst_one.id)
    second_one = IdentityService.recommendation_identity(db_session, analyst_one.id)
    first_two = IdentityService.recommendation_identity(db_session, analyst_two.id)
    trade_one = IdentityService.trade_identity(db_session, trader_one.id)
    trade_two = IdentityService.trade_identity(db_session, trader_two.id)

    assert first_one[1] == 1
    assert second_one[1] == 2
    assert first_two[1] == 1
    assert trade_one[1] == 1
    assert trade_two[1] == 1
    assert analyst_one.analyst_code == "AN-000001"
    assert analyst_two.analyst_code == "AN-000002"
    assert trader_one.user_code == "USR-000003"
    assert trader_two.user_code == "USR-000004"


def test_display_ref_requires_scope_context():
    assert IdentityService.display_ref("AN-000001", "R", 7) == "AN-000001/R-000007"
    assert IdentityService.display_ref("USR-000002", "T", 8) == "USR-000002/T-000008"
