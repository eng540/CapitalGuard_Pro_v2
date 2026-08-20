import pytest

from capitalguard.application.services.historical_owner_review_service import (
    HistoricalOwnerReviewError,
    HistoricalOwnerReviewService,
)
from capitalguard.application.services.historical_signal_service import HistoricalSignalService


def make_batch(db_session, *, accepted=1, rejected=0):
    batch = HistoricalSignalService().create_import_batch(
        db_session,
        source_kind="TELEGRAM_FORWARD",
        manifest=[{"message_id": 1}],
    )
    batch.accepted_records = accepted
    batch.rejected_records = rejected
    return batch


def test_owner_review_approves_only_after_dry_run(db_session):
    batch = make_batch(db_session, accepted=1)
    reviewed = HistoricalOwnerReviewService().review_batch(
        db_session,
        batch_id=batch.id,
        reviewer_user_id=99,
        approved=True,
        note="Reviewed source and preview",
    )
    assert reviewed.status == "VALIDATED"
    assert reviewed.metadata_json["owner_review"]["approved"] is True


def test_owner_review_rejects_empty_approval_and_allows_rejection(db_session):
    batch = make_batch(db_session, accepted=0)
    service = HistoricalOwnerReviewService()
    with pytest.raises(HistoricalOwnerReviewError, match="no accepted"):
        service.review_batch(
            db_session,
            batch_id=batch.id,
            reviewer_user_id=99,
            approved=True,
        )
    reviewed = service.review_batch(
        db_session,
        batch_id=batch.id,
        reviewer_user_id=99,
        approved=False,
        note="No accepted records",
    )
    assert reviewed.status == "REJECTED"


def test_owner_review_is_not_repeatable_after_validation(db_session):
    batch = make_batch(db_session, accepted=1)
    service = HistoricalOwnerReviewService()
    service.review_batch(db_session, batch_id=batch.id, reviewer_user_id=99, approved=True)
    with pytest.raises(HistoricalOwnerReviewError, match="Only a dry-run"):
        service.review_batch(db_session, batch_id=batch.id, reviewer_user_id=99, approved=True)
