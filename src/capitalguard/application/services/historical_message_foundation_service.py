from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import (
    HistoricalCanonicalMessage,
    HistoricalForwardReceipt,
    HistoricalMessageRelationship,
    HistoricalMessageRevision,
)


class HistoricalMessageFoundationError(ValueError):
    pass


class HistoricalMessageFoundationService:
    """G1 source memory only; it deliberately performs no semantic extraction or replay."""

    _REVIEW_STATUSES = {"PENDING", "ACCEPTED", "REJECTED", "OVERRIDDEN"}

    @staticmethod
    def _utc(value: datetime | None) -> datetime:
        value = value or datetime.now(timezone.utc)
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    @staticmethod
    def _safe_classify(raw_text: str | None, origin: str, reply_to: int | None) -> tuple[str, Decimal]:
        text = (raw_text or "").upper()
        if reply_to:
            return "REPLY", Decimal("1.0000")
        if origin.upper() == "FORWARD":
            return "FORWARD", Decimal("1.0000")
        if not text.strip():
            return "MEDIA", Decimal("0.5000")
        if any(token in text for token in ("MOVE SL", "STOP MOVED", "TP1 HIT", "TP2 HIT", "CLOSED", "CANCEL")):
            return "POSSIBLE_UPDATE", Decimal("0.7000")
        if any(token in text for token in (" LONG", " SHORT", "ENTRY", "STOP LOSS", "\nSL", "TP1")):
            return "POSSIBLE_RECOMMENDATION", Decimal("0.6000")
        if any(token in text for token in ("RESULT", "PROFIT", "LOSS", "PNL", "النتيجة")):
            return "RESULT_CLAIM", Decimal("0.6000")
        return "TEXT", Decimal("0.5000")

    def record_receipt(self, session: Session, *, receipt: HistoricalForwardReceipt) -> HistoricalMessageRevision:
        if receipt.source_chat_id is None or receipt.source_message_id is None:
            raise HistoricalMessageFoundationError("Canonical source identity requires source chat and message identifiers")
        message = session.execute(select(HistoricalCanonicalMessage).where(
            HistoricalCanonicalMessage.source_kind == "TELEGRAM",
            HistoricalCanonicalMessage.source_chat_id == receipt.source_chat_id,
            HistoricalCanonicalMessage.external_message_id == receipt.source_message_id,
        )).scalar_one_or_none()
        observed_at = self._utc(receipt.created_at or receipt.source_message_timestamp)
        if message is None:
            message = HistoricalCanonicalMessage(
                source_kind="TELEGRAM",
                source_chat_id=receipt.source_chat_id,
                external_message_id=receipt.source_message_id,
                ingestion_mode="HISTORICAL",
                first_observed_at=observed_at,
            )
            session.add(message)
            session.flush()
        existing = session.execute(select(HistoricalMessageRevision).where(
            HistoricalMessageRevision.message_id == message.id,
            HistoricalMessageRevision.content_hash == receipt.content_hash,
        )).scalar_one_or_none()
        if existing is not None:
            return existing
        classification, confidence = self._safe_classify(receipt.raw_text, receipt.source_origin_type, receipt.source_reply_to_message_id)
        revision = HistoricalMessageRevision(
            message_id=message.id,
            revision_number=message.latest_revision_number + 1,
            observed_at=observed_at,
            source_timestamp=receipt.source_message_timestamp,
            source_edit_date=receipt.source_edit_date,
            content_hash=receipt.content_hash,
            raw_text=receipt.raw_text,
            source_origin_type=receipt.source_origin_type,
            source_reply_to_message_id=receipt.source_reply_to_message_id,
            safe_classification=classification,
            classification_confidence=confidence,
            receipt_id=receipt.id,
            evidence_id=receipt.evidence_id,
            provenance_json={"batch_id": receipt.batch_id, "receiver_chat_id": receipt.receiver_chat_id, "receiver_message_id": receipt.receiver_message_id},
        )
        session.add(revision)
        message.latest_revision_number += 1
        session.flush()
        return revision

    def propose_relationship(self, session: Session, *, source_message_id: int, target_message_id: int, relationship_type: str, confidence: Decimal, evidence: dict, method: str = "G1_SAFE_RULES") -> HistoricalMessageRelationship:
        if source_message_id == target_message_id:
            raise HistoricalMessageFoundationError("A message cannot relate to itself")
        if not Decimal("0") <= confidence <= Decimal("1"):
            raise HistoricalMessageFoundationError("Relationship confidence must be between zero and one")
        existing = session.execute(select(HistoricalMessageRelationship).where(
            HistoricalMessageRelationship.source_message_id == source_message_id,
            HistoricalMessageRelationship.target_message_id == target_message_id,
            HistoricalMessageRelationship.relationship_type == relationship_type,
            HistoricalMessageRelationship.method == method,
        )).scalar_one_or_none()
        if existing is not None:
            return existing
        relationship = HistoricalMessageRelationship(source_message_id=source_message_id, target_message_id=target_message_id, relationship_type=relationship_type, confidence_score=confidence, evidence_json=evidence, method=method)
        session.add(relationship)
        session.flush()
        return relationship

    def review_relationship(self, session: Session, *, relationship_id: int, reviewer_user_id: int, status: str, note: str | None = None) -> HistoricalMessageRelationship:
        if status not in self._REVIEW_STATUSES - {"PENDING"}:
            raise HistoricalMessageFoundationError("Invalid relationship review status")
        relationship = session.get(HistoricalMessageRelationship, relationship_id)
        if relationship is None:
            raise HistoricalMessageFoundationError("Relationship does not exist")
        relationship.review_status = status
        relationship.reviewed_by_user_id = reviewer_user_id
        relationship.reviewed_at = datetime.now(timezone.utc)
        relationship.review_note = note
        session.flush()
        return relationship
