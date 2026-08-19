import pytest

from capitalguard.application.services.analyst_profile_service import AnalystProfileService
from capitalguard.domain.entities import UserType
from capitalguard.infrastructure.db.repository import UserRepository
from capitalguard.interfaces.telegram.commands import _parse_profile_updates


def test_profile_update_rejects_overlong_bio(db_session):
    analyst = UserRepository(db_session).find_or_create(
        telegram_id=4201,
        user_type=UserType.ANALYST,
        first_name="Validated Analyst",
    )
    with pytest.raises(ValueError):
        AnalystProfileService().update_profile(db_session, analyst, bio="x" * 2001)


def test_profile_parser_normalizes_public_fields():
    result = _parse_profile_updates(
        "name=Alpha Desk | bio=BTC swing setups | market=Crypto | style=Swing | public=yes"
    )
    assert result == {
        "public_name": "Alpha Desk",
        "bio": "BTC swing setups",
        "specialty_market": "Crypto",
        "strategy_style": "Swing",
        "is_public": True,
    }


def test_profile_parser_rejects_unknown_field():
    with pytest.raises(ValueError):
        _parse_profile_updates("password=secret")
