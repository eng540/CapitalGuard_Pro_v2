import pytest

from capitalguard.application.services.historical_channel_claim_service import (
    HistoricalChannelClaimError,
    HistoricalChannelClaimService,
)
from capitalguard.infrastructure.db.models import ChannelCatalog, HistoricalShadowChannel


def shadow(db_session):
    item = HistoricalShadowChannel(
        telegram_channel_id=-100999,
        title="Unclaimed Source",
        claim_status="UNCLAIMED",
        metadata_json={},
    )
    db_session.add(item)
    db_session.flush()
    return item


def catalog(db_session):
    item = ChannelCatalog(
        channel_code="CH-CLAIM-01",
        public_ref="CH-CLAIM-01",
        title="Canonical Source",
        telegram_channel_id=-100999,
        is_active=True,
    )
    db_session.add(item)
    db_session.flush()
    return item


def test_claim_requires_proof_and_owner_review(db_session):
    item = shadow(db_session)
    service = HistoricalChannelClaimService()

    with pytest.raises(HistoricalChannelClaimError, match="proof"):
        service.request_claim(
            db_session,
            shadow_channel_id=item.id,
            requester_user_id=10,
            proof_type="",
            proof_ref="",
        )

    service.request_claim(
        db_session,
        shadow_channel_id=item.id,
        requester_user_id=10,
        proof_type="CHANNEL_OWNER",
        proof_ref="telegram-proof-1",
    )
    assert item.claim_status == "CLAIM_PENDING"

    with pytest.raises(HistoricalChannelClaimError, match="canonical"):
        service.review_claim(
            db_session,
            shadow_channel_id=item.id,
            reviewer_user_id=99,
            approved=True,
        )


def test_claim_verification_and_release_are_explicit(db_session):
    item = shadow(db_session)
    canonical = catalog(db_session)
    service = HistoricalChannelClaimService()

    service.request_claim(
        db_session,
        shadow_channel_id=item.id,
        requester_user_id=10,
        proof_type="SUBSCRIBER_FORWARD",
        proof_ref="forward-receipt-1",
    )
    service.review_claim(
        db_session,
        shadow_channel_id=item.id,
        reviewer_user_id=99,
        approved=True,
        canonical_channel_catalog_id=canonical.id,
        note="Owner proof reviewed",
    )
    assert item.claim_status == "VERIFIED"
    assert item.canonical_channel_catalog_id == canonical.id

    service.release_claim(
        db_session,
        shadow_channel_id=item.id,
        reviewer_user_id=99,
        note="Ownership changed",
    )
    assert item.claim_status == "RELEASED"
    assert item.canonical_channel_catalog_id is None


def test_rejected_claim_can_be_submitted_again(db_session):
    item = shadow(db_session)
    service = HistoricalChannelClaimService()
    service.request_claim(
        db_session,
        shadow_channel_id=item.id,
        requester_user_id=10,
        proof_type="OWNER_ATTESTATION",
        proof_ref="proof-a",
    )
    service.review_claim(
        db_session,
        shadow_channel_id=item.id,
        reviewer_user_id=99,
        approved=False,
        note="Insufficient proof",
    )
    assert item.claim_status == "REJECTED"
    service.request_claim(
        db_session,
        shadow_channel_id=item.id,
        requester_user_id=10,
        proof_type="OWNER_ATTESTATION",
        proof_ref="proof-b",
    )
    assert item.claim_status == "CLAIM_PENDING"
