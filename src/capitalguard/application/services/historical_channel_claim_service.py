from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import HistoricalShadowChannel


class HistoricalChannelClaimError(ValueError):
    """Raised when a shadow-channel claim violates its state or proof contract."""


class HistoricalChannelClaimService:
    """Keeps discovery, claim, and verification separate from canonical identity."""

    CLAIMABLE = {"UNCLAIMED", "REJECTED", "RELEASED"}

    def request_claim(
        self,
        session: Session,
        *,
        shadow_channel_id: int,
        requester_user_id: int,
        proof_type: str,
        proof_ref: str,
    ) -> HistoricalShadowChannel:
        shadow = session.get(HistoricalShadowChannel, shadow_channel_id)
        if shadow is None:
            raise HistoricalChannelClaimError("Shadow channel does not exist")
        if shadow.claim_status not in self.CLAIMABLE:
            raise HistoricalChannelClaimError("Shadow channel is not claimable in its current state")
        if not requester_user_id:
            raise HistoricalChannelClaimError("requester_user_id is required")
        if not proof_type or not proof_ref:
            raise HistoricalChannelClaimError("claim proof type and reference are required")
        metadata = dict(shadow.metadata_json or {})
        metadata["claim_request"] = {
            "requester_user_id": requester_user_id,
            "proof_type": proof_type.strip().upper(),
            "proof_ref": proof_ref,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
        shadow.metadata_json = metadata
        shadow.claim_status = "CLAIM_PENDING"
        session.flush()
        return shadow

    def review_claim(
        self,
        session: Session,
        *,
        shadow_channel_id: int,
        reviewer_user_id: int,
        approved: bool,
        canonical_channel_catalog_id: int | None = None,
        note: str | None = None,
    ) -> HistoricalShadowChannel:
        shadow = session.get(HistoricalShadowChannel, shadow_channel_id)
        if shadow is None:
            raise HistoricalChannelClaimError("Shadow channel does not exist")
        if shadow.claim_status != "CLAIM_PENDING":
            raise HistoricalChannelClaimError("Only pending claims can be reviewed")
        if not reviewer_user_id:
            raise HistoricalChannelClaimError("reviewer_user_id is required")
        if approved and canonical_channel_catalog_id is None:
            raise HistoricalChannelClaimError("Approved claim requires canonical channel mapping")
        metadata = dict(shadow.metadata_json or {})
        metadata["claim_review"] = {
            "reviewer_user_id": reviewer_user_id,
            "approved": bool(approved),
            "note": note,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
        shadow.metadata_json = metadata
        shadow.claim_status = "VERIFIED" if approved else "REJECTED"
        if approved:
            shadow.canonical_channel_catalog_id = canonical_channel_catalog_id
        session.flush()
        return shadow

    def release_claim(
        self,
        session: Session,
        *,
        shadow_channel_id: int,
        reviewer_user_id: int,
        note: str | None = None,
    ) -> HistoricalShadowChannel:
        shadow = session.get(HistoricalShadowChannel, shadow_channel_id)
        if shadow is None:
            raise HistoricalChannelClaimError("Shadow channel does not exist")
        if shadow.claim_status != "VERIFIED":
            raise HistoricalChannelClaimError("Only verified claims can be released")
        metadata = dict(shadow.metadata_json or {})
        metadata["claim_release"] = {
            "reviewer_user_id": reviewer_user_id,
            "note": note,
            "released_at": datetime.now(timezone.utc).isoformat(),
        }
        shadow.metadata_json = metadata
        shadow.claim_status = "RELEASED"
        shadow.canonical_channel_catalog_id = None
        session.flush()
        return shadow
