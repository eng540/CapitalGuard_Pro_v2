from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import HistoricalContentInterpretation, HistoricalMessageRevision


class HistoricalContentUnderstandingService:
    """G2 deterministic content understanding; deliberately never returns financial intent."""

    CLASSIFIER_VERSION = "g2-rules-v1"

    @staticmethod
    def _classify(text: str | None) -> tuple[str, Decimal, dict, str | None]:
        normalized = (text or "").strip().lower()
        if not normalized:
            return "MEDIA", Decimal("0.5000"), {"topics": []}, "NO_TEXT"
        if any(token in normalized for token in ("خبر", "news", "breaking", "إعلان")):
            return "NEWS", Decimal("0.8500"), {"topics": ["news"]}, None
        if any(token in normalized for token in ("تحليل", "analysis", "outlook", "نظرة")):
            return "MARKET_COMMENTARY", Decimal("0.8000"), {"topics": ["analysis"]}, None
        if any(token in normalized for token in ("إعلان", "عرض", "promo", "join our", "اشترك")):
            return "ADVERTISEMENT", Decimal("0.7500"), {"topics": ["promotion"]}, None
        if any(token in normalized for token in ("نتيجة", "result", "pnl", "profit", "loss")):
            return "RESULT_CLAIM", Decimal("0.7000"), {"topics": ["claimed_result"]}, None
        return "UNKNOWN", Decimal("0.2500"), {"topics": []}, "INSUFFICIENT_NON_FINANCIAL_CONTEXT"

    def interpret_revision(self, session: Session, *, revision_id: int) -> HistoricalContentInterpretation:
        revision = session.get(HistoricalMessageRevision, revision_id)
        if revision is None:
            raise ValueError("Historical message revision does not exist")
        existing = session.execute(select(HistoricalContentInterpretation).where(
            HistoricalContentInterpretation.revision_id == revision_id,
            HistoricalContentInterpretation.classifier_version == self.CLASSIFIER_VERSION,
        )).scalar_one_or_none()
        if existing is not None:
            return existing
        content_type, confidence, meaning, ambiguity = self._classify(revision.raw_text)
        interpretation = HistoricalContentInterpretation(
            revision_id=revision_id,
            content_type=content_type,
            confidence_score=confidence,
            classifier_version=self.CLASSIFIER_VERSION,
            meaning_json=meaning,
            ambiguity_reason=ambiguity,
            provenance_json={"revision_id": revision.id, "content_hash": revision.content_hash, "source_span": None, "classifier_version": self.CLASSIFIER_VERSION},
        )
        session.add(interpretation)
        session.flush()
        return interpretation
