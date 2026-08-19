from capitalguard.application.services.identity_service import IdentityService
from capitalguard.infrastructure.db.models import ChannelCatalog


def test_channel_catalog_is_canonical_and_stable(db_session):
    first = IdentityService.ensure_channel_catalog(db_session, -100123456789, "Primary")
    second = IdentityService.ensure_channel_catalog(db_session, -100123456789, "Renamed")
    other = IdentityService.ensure_channel_catalog(db_session, -100987654321, "Secondary")

    assert first.id == second.id
    assert first.channel_code == second.channel_code
    assert first.public_ref == second.public_ref
    assert first.title == "Primary"
    assert other.channel_code != first.channel_code
    assert db_session.query(ChannelCatalog).count() == 2
