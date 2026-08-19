from capitalguard.application.services.publication_outbox_service import PublicationOutboxService
from capitalguard.infrastructure.db.models import RecommendationChannelRef


def test_outbox_creates_one_channel_reference_per_publication_target(db_session):
    service = PublicationOutboxService(repo=None, notifier=None)

    first = service.enqueue_create_deliveries(
        db_session,
        recommendation_id=901,
        channel_ids={-1001, -1002},
    )
    second = service.enqueue_create_deliveries(
        db_session,
        recommendation_id=901,
        channel_ids={-1001, -1002},
    )

    refs = db_session.query(RecommendationChannelRef).filter_by(recommendation_id=901).all()
    assert len(first) == 2
    assert len(second) == 2
    assert len(refs) == 2
    assert {ref.channel_sequence for ref in refs} == {1}
    assert len({ref.channel_catalog_id for ref in refs}) == 2
