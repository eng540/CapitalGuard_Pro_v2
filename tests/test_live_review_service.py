import pytest

from capitalguard.application.services.live_review_service import LiveReviewService


def test_live_eligible_plan_requires_explicit_review_action():
    service = LiveReviewService()
    plan = service.prepare({
        "mode": "LIVE_ELIGIBLE",
        "route": "LIVE_REVIEW",
        "reason_codes": ["FRESH_SOURCE_WITHIN_ENTRY_ENVELOPE"],
    })
    assert "ACCEPT_LIVE_REVIEW" in plan.allowed_actions
    assert plan.creates_live_entity is False
    outcome = service.apply(plan, "ACCEPT_LIVE_REVIEW")
    assert outcome.status == "LIVE_REVIEW_ACCEPTED"
    assert outcome.creates_live_entity is False


def test_stale_signal_can_be_recovered_or_imported_without_copy_trading():
    service = LiveReviewService()
    plan = service.prepare({
        "mode": "LIVE_STALE",
        "route": "HISTORICAL_CANDIDATE",
        "reason_codes": ["STALE_LIVE_CANDIDATE"],
    })
    assert "RECOVER_REVIEW" in plan.allowed_actions
    assert service.apply(plan, "RECOVER_REVIEW").creates_live_entity is False
    assert service.apply(plan, "IMPORT_HISTORICAL").status == "HISTORICAL_REVIEW_REQUESTED"


def test_conflict_cannot_be_accepted_as_live():
    service = LiveReviewService()
    plan = service.prepare({"mode": "CONFLICT_REVIEW", "route": "QUARANTINE"})
    with pytest.raises(ValueError, match="not allowed"):
        service.apply(plan, "ACCEPT_LIVE_REVIEW")
    assert service.apply(plan, "REVIEW_CONFLICT").status == "CONFLICT_REVIEW_REQUIRED"
