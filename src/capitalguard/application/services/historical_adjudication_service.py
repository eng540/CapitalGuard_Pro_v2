from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session
from capitalguard.infrastructure.db.models import HistoricalFinancialCandidate, HistoricalMessageRelationship, HistoricalMessageRevision, HistoricalRecommendationDraft

class HistoricalAdjudicationService:
    REQUIRED_NEW = {"ASSET", "DIRECTION", "ENTRY"}
    def adjudicate(self, session: Session, *, revision_id: int):
        candidates = session.execute(select(HistoricalFinancialCandidate).join(HistoricalFinancialCandidate.interpretation).where(HistoricalFinancialCandidate.interpretation.has(revision_id=revision_id))).scalars().all()
        existing = session.execute(select(HistoricalRecommendationDraft).where(HistoricalRecommendationDraft.revision_id == revision_id, HistoricalRecommendationDraft.draft_kind == "NEW_RECOMMENDATION")).scalar_one_or_none()
        if existing: return existing
        by_field = {}
        for item in candidates: by_field.setdefault(item.field_type, []).append(item)
        accepted = {field: rows for field, rows in by_field.items() if len(rows) == 1 and rows[0].review_status == "ACCEPTED" and rows[0].status == "CANDIDATE"}
        missing = sorted(self.REQUIRED_NEW - set(accepted))
        conflict = any(item.status == "CONFLICT" or item.review_status == "REVIEW_REQUIRED" for item in candidates)
        status = "REVIEW_REQUIRED" if missing or conflict else "DRAFT"
        chain = {field: [item.id for item in rows] for field, rows in by_field.items()}
        draft = HistoricalRecommendationDraft(revision_id=revision_id, draft_kind="NEW_RECOMMENDATION", confidence_score=Decimal("0.9000") if status == "DRAFT" else Decimal("0.0000"), status=status, evidence_chain_json=chain, adjudication_reason=("MISSING_ACCEPTED:" + ",".join(missing)) if missing else ("CONFLICTING_CANDIDATES" if conflict else None))
        session.add(draft); session.flush(); return draft
    def review(self, session: Session, *, draft_id: int, reviewer_user_id: int, decision: str, note: str | None = None, override: dict | None = None):
        if decision not in {"ACCEPTED", "REJECTED", "SUPERSEDED", "CANCELLED"}: raise ValueError("Invalid draft review decision")
        draft = session.get(HistoricalRecommendationDraft, draft_id)
        if draft is None: raise ValueError("Draft does not exist")
        draft.status, draft.reviewed_by_user_id, draft.reviewed_at, draft.review_note, draft.override_json = decision, reviewer_user_id, datetime.now(timezone.utc), note, override
        session.flush(); return draft

    def adjudicate_lifecycle(self, session: Session, *, revision_id: int, related_draft_id: int):
        related = session.get(HistoricalRecommendationDraft, related_draft_id)
        if related is None or related.status != "ACCEPTED":
            raise ValueError("Lifecycle draft requires an accepted related draft")
        revision = session.get(HistoricalMessageRevision, revision_id)
        related_revision = session.get(HistoricalMessageRevision, related.revision_id)
        if revision is None or related_revision is None:
            raise ValueError("Lifecycle draft requires canonical message revisions")
        existing = session.execute(select(HistoricalRecommendationDraft).where(HistoricalRecommendationDraft.revision_id == revision_id, HistoricalRecommendationDraft.related_draft_id == related_draft_id)).scalar_one_or_none()
        if existing: return existing
        candidates = session.execute(select(HistoricalFinancialCandidate).join(HistoricalFinancialCandidate.interpretation).where(HistoricalFinancialCandidate.interpretation.has(revision_id=revision_id))).scalars().all()
        fields = {item.field_type for item in candidates if item.review_status == "ACCEPTED" and item.status == "CANDIDATE"}
        relation = session.execute(select(HistoricalMessageRelationship).where(HistoricalMessageRelationship.source_message_id == revision.message_id, HistoricalMessageRelationship.target_message_id == related_revision.message_id, HistoricalMessageRelationship.review_status == "ACCEPTED")).scalar_one_or_none()
        kind = "SL_UPDATE" if "STOP_LOSS" in fields else "TP_UPDATE" if "TARGET" in fields else "ENTRY_UPDATE" if "ENTRY" in fields else "UNKNOWN"
        status = "DRAFT" if relation and kind != "UNKNOWN" else "REVIEW_REQUIRED"
        draft = HistoricalRecommendationDraft(revision_id=revision_id, related_draft_id=related_draft_id, draft_kind=kind, confidence_score=Decimal("0.8000") if status == "DRAFT" else Decimal("0"), status=status, evidence_chain_json={"candidate_ids": [item.id for item in candidates], "relationship_id": relation.id if relation else None}, adjudication_reason=None if status == "DRAFT" else "RELATIONSHIP_OR_LIFECYCLE_EVIDENCE_INSUFFICIENT")
        session.add(draft); session.flush(); return draft
