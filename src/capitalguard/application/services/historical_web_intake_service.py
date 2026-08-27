from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import HistoricalForwardReceipt, HistoricalImportBatch, HistoricalMessageRevision, HistoricalRecommendationDraft, HistoricalSignal, HistoricalSignalEvent, HistoricalSignalEvidence
from capitalguard.application.services.historical_forwarding_service import HistoricalForwardingService
from capitalguard.application.services.historical_message_foundation_service import HistoricalMessageFoundationService
from capitalguard.application.services.historical_parser_service import HistoricalParserService
from capitalguard.application.services.historical_semantic_materialization_service import HistoricalSemanticMaterializationService
from capitalguard.application.services.historical_signal_service import HistoricalSignalService, HistoricalSignalValidationError
from capitalguard.application.services.parsing_service import ParsingService
from capitalguard.infrastructure.db.repository import ParsingRepository


class HistoricalWebIntakeError(ValueError):
    """Raised when a Web historical intake cannot be staged safely."""


class HistoricalWebIntakeService:
    """Thin Web orchestration facade over the existing historical services.

    It owns no new persistence model. Existing batches and receipts remain the
    source of truth; this class only adapts Web 1..N input into them and records
    a per-item preview in the existing receipt metadata.
    """

    ALLOWED_SOURCE_KINDS = {"TELEGRAM_EXPORT", "MANUAL_ADMIN_IMPORT"}
    ALLOWED_INPUT_MODES = {"PASTE", "UPLOAD", "TELEGRAM_EXPORT"}
    MAX_ITEMS = 5000
    MAX_TEXT_LENGTH = 50_000

    def __init__(self):
        self.signal_service = HistoricalSignalService()
        self.forwarding_service = HistoricalForwardingService(signal_service=self.signal_service)
        self.message_foundation = HistoricalMessageFoundationService()
        self.semantic_service = HistoricalSemanticMaterializationService()
        self.parser = HistoricalParserService(ParsingService(ParsingRepository))

    @staticmethod
    def _utc(value: datetime | None) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _content_hash(raw_text: str | None, media: dict[str, Any] | None) -> str:
        payload = {
            "raw_text": raw_text or "",
            "media": media or {},
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _json_safe(value: Any):
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, dict):
            return {key: HistoricalWebIntakeService._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [HistoricalWebIntakeService._json_safe(item) for item in value]
        return value

    @staticmethod
    def _item_status(parsed_status: str, projection: dict[str, Any] | None) -> str:
        if projection and projection.get("status"):
            return str(projection["status"]).upper()
        return "SUCCESS" if parsed_status == "PARSED" else "INCOMPLETE"

    @staticmethod
    def _missing_from_parser(data: dict[str, Any]) -> list[str]:
        required = ("asset", "side", "entry", "stop_loss", "targets")
        return [field for field in required if not data.get(field)]

    def _preview_without_source(self, raw_text: str | None) -> dict[str, Any]:
        parsed = self.parser.parse(raw_text or "")
        data = parsed.data or {}
        missing = self._missing_from_parser(data) if parsed.parse_status == "PARSED" else ["parse"]
        return {
            "status": "SUCCESS" if parsed.parse_status == "PARSED" and not missing else "INCOMPLETE",
            "parse_status": parsed.parse_status,
            "canonical": data,
            "missing_fields": missing,
            "conflicting_fields": data.get("conflicting_fields", []),
            "source_verification": "UNVERIFIED",
        }

    def _source_verification(self, receipt: HistoricalForwardReceipt, metadata: dict[str, Any]) -> str:
        if metadata.get("source_verification"):
            return str(metadata["source_verification"])
        return "VERIFIED_PROVENANCE" if receipt.source_chat_id is not None and receipt.source_message_id is not None else "UNVERIFIED"

    def _stored_semantic_preview(self, session: Session, receipt: HistoricalForwardReceipt) -> dict[str, Any] | None:
        metadata = receipt.metadata_json or {}
        direct_preview = metadata.get("historical_preview")
        if isinstance(direct_preview, dict) and direct_preview:
            return direct_preview
        revision = session.execute(
            select(HistoricalMessageRevision)
            .where(HistoricalMessageRevision.receipt_id == receipt.id)
            .order_by(HistoricalMessageRevision.id.desc())
        ).scalars().first()
        if revision is None:
            return None
        draft = session.execute(
            select(HistoricalRecommendationDraft)
            .where(HistoricalRecommendationDraft.revision_id == revision.id)
            .order_by(HistoricalRecommendationDraft.id.desc())
        ).scalars().first()
        chain = (draft.evidence_chain_json or {}) if draft is not None else {}
        materialization = chain.get("semantic_materialization")
        if not isinstance(materialization, dict):
            return None
        canonical = dict(materialization.get("canonical") or {})
        if canonical.get("side") is None and canonical.get("direction") is not None:
            canonical["side"] = canonical["direction"]
        return {
            "status": materialization.get("status", "INCOMPLETE"),
            "parse_status": "MATERIALIZED",
            "canonical": canonical,
            "missing_fields": list(materialization.get("missing_fields") or []),
            "conflicting_fields": list(materialization.get("conflicting_fields") or []),
            "source_verification": self._source_verification(receipt, metadata),
            "materialization_version": materialization.get("materialization_version"),
            "extraction_source": "HISTORICAL_RECOMMENDATION_DRAFT",
        }

    def _item_response(self, session: Session, receipt: HistoricalForwardReceipt, fallback_order: int | None = None) -> dict[str, Any]:
        metadata = receipt.metadata_json or {}
        preview = self._stored_semantic_preview(session, receipt) or {}
        order = metadata.get("item_order") or fallback_order
        item_key = metadata.get("item_key") or (f"message-{receipt.source_message_id}" if receipt.source_message_id is not None else f"item-{order or receipt.id}")
        return {
            "id": receipt.id,
            "order": order,
            "item_key": item_key,
            "status": receipt.validation_status,
            "semantic_status": preview.get("status", "NOT_PROCESSED"),
            "parse_status": preview.get("parse_status"),
            "source_verification": preview.get("source_verification") or self._source_verification(receipt, metadata),
            "source_chat_id": receipt.source_chat_id,
            "source_message_id": receipt.source_message_id,
            "source_timestamp": receipt.source_message_timestamp.isoformat() if receipt.source_message_timestamp else None,
            "raw_text": receipt.raw_text,
            "content_hash": receipt.content_hash,
            "missing_fields": preview.get("missing_fields", []),
            "conflicting_fields": preview.get("conflicting_fields", []),
            "canonical": preview.get("canonical", {}),
            "rejection_reason": receipt.rejection_reason,
            "metadata": {
                "input_mode": metadata.get("input_mode"),
                "source_uri": metadata.get("source_uri"),
                "media": metadata.get("media"),
                "related_item_key": metadata.get("related_item_key"),
            },
        }

    @staticmethod
    def _result_contract(batch: HistoricalImportBatch) -> dict[str, Any]:
        metadata = batch.metadata_json or {}
        processed_count = int(metadata.get("processed_count", batch.total_records or 0))
        changed_count = int(metadata.get("changed_count", max(0, (batch.total_records or 0) - (batch.rejected_records or 0))))
        result_status = str(metadata.get("result_status") or ("NO_CHANGE" if changed_count == 0 else "PARTIAL_CHANGE" if (batch.rejected_records or 0) else "CHANGED"))
        return {
            "processed_count": max(0, processed_count),
            "changed_count": max(0, changed_count),
            "result_status": result_status,
        }

    def _batch_response(self, session: Session, batch: HistoricalImportBatch) -> dict[str, Any]:
        receipts = session.execute(
            select(HistoricalForwardReceipt)
            .where(HistoricalForwardReceipt.batch_id == batch.id)
            .order_by(HistoricalForwardReceipt.id)
        ).scalars().all()
        items = [self._item_response(session, receipt, fallback_order=index) for index, receipt in enumerate(receipts, start=1)]
        return {
            "ok": True,
            "batch": {
                "id": batch.id,
                "ref": batch.batch_ref,
                "status": batch.status,
                "source_kind": batch.source_kind,
                "total_records": batch.total_records,
                "accepted_records": batch.accepted_records,
                "rejected_records": batch.rejected_records,
                **self._result_contract(batch),
                "created_at": batch.created_at.isoformat() if batch.created_at else None,
                "metadata": batch.metadata_json or {},
                "items": items,
            },
        }

    def create_batch(
        self,
        session: Session,
        *,
        requested_by_user_id: int,
        source_kind: str,
        input_mode: str,
        items: list[dict[str, Any]],
        is_partial: bool = False,
        batch_label: str | None = None,
    ) -> dict[str, Any]:
        source = source_kind.strip().upper()
        mode = input_mode.strip().upper()
        if source not in self.ALLOWED_SOURCE_KINDS:
            raise HistoricalWebIntakeError(f"Unsupported Web historical source kind: {source_kind}")
        if mode not in self.ALLOWED_INPUT_MODES:
            raise HistoricalWebIntakeError(f"Unsupported Web historical input mode: {input_mode}")
        if not requested_by_user_id:
            raise HistoricalWebIntakeError("requested_by_user_id is required")
        if not items or len(items) > self.MAX_ITEMS:
            raise HistoricalWebIntakeError(f"items must contain between 1 and {self.MAX_ITEMS} records")

        batch = self.signal_service.create_import_batch(
            session,
            source_kind=source,
            manifest=[],
            requested_by_user_id=requested_by_user_id,
            metadata={
                "input_mode": mode,
                "is_partial": bool(is_partial),
                "batch_label": (batch_label or "").strip()[:255] or None,
                "intake_status": "STAGING",
                "pipeline": "WEB_HISTORICAL_INTAKE",
                "item_count_requested": len(items),
            },
        )
        batch.status = "STAGING"
        session.flush()

        receiver_chat_id = 10_000_000_000 + int(requested_by_user_id)
        seen_hashes: set[str] = set()
        staged = 0
        rejected = 0
        for order, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                rejected += 1
                continue
            raw_text = item.get("raw_text")
            raw_text = str(raw_text) if raw_text is not None else None
            if raw_text is not None and len(raw_text) > self.MAX_TEXT_LENGTH:
                raw_text = raw_text[: self.MAX_TEXT_LENGTH]
            media = item.get("media") if isinstance(item.get("media"), dict) else None
            content_hash = self._content_hash(raw_text, media)
            source_chat_id = self._safe_int(item.get("source_chat_id"))
            source_message_id = self._safe_int(item.get("source_message_id"))
            revision = max(0, self._safe_int(item.get("source_message_revision")) or 0)
            source_timestamp = item.get("source_message_timestamp")
            if isinstance(source_timestamp, str):
                try:
                    source_timestamp = datetime.fromisoformat(source_timestamp.replace("Z", "+00:00"))
                except ValueError:
                    source_timestamp = None
            source_timestamp = self._utc(source_timestamp)
            source_origin_type = str(item.get("source_origin_type") or mode).upper()[:40]
            metadata = {
                "item_order": order,
                "item_key": str(item.get("item_key") or f"item-{order}")[:120],
                "input_mode": mode,
                "source_uri": str(item.get("source_uri") or "")[:500] or None,
                "media": media,
                "related_item_key": str(item.get("related_item_key") or "")[:120] or None,
                "partial_input": bool(is_partial),
                "source_verification": "VERIFIED_PROVENANCE" if source_chat_id and source_message_id else "UNVERIFIED",
            }
            status = "STAGED"
            rejection_reason = None
            if content_hash in seen_hashes:
                status = "DUPLICATE"
                rejection_reason = "Duplicate content within intake batch"
            seen_hashes.add(content_hash)

            receipt = HistoricalForwardReceipt(
                batch_id=batch.id,
                forwarding_user_id=requested_by_user_id,
                receiver_chat_id=receiver_chat_id,
                receiver_message_id=batch.id * 1_000_000 + order,
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
                source_message_revision=revision,
                source_origin_type=source_origin_type,
                source_message_timestamp=source_timestamp,
                source_edit_date=None,
                source_reply_to_message_id=self._safe_int(item.get("source_reply_to_message_id")),
                raw_text=raw_text,
                content_hash=content_hash,
                validation_status=status,
                rejection_reason=rejection_reason,
                metadata_json=metadata,
            )
            session.add(receipt)
            session.flush()
            if status == "DUPLICATE":
                rejected += 1
                continue

            try:
                revision_row = self.message_foundation.record_receipt(session, receipt=receipt)
                projection = self.semantic_service.materialize_revision(
                    session,
                    revision_id=revision_row.id,
                    image_provenance={
                        "source_chat_id": source_chat_id,
                        "source_message_id": source_message_id,
                        "input_mode": mode,
                        "source_verification": metadata["source_verification"],
                    },
                )
                source_verification = metadata["source_verification"]
                safe_projection = self._json_safe(projection)
                receipt.metadata_json = {
                    **metadata,
                    "historical_preview": {
                        **safe_projection,
                        "source_verification": source_verification,
                    },
                }
                staged += 1
            except (HistoricalSignalValidationError, ValueError, TypeError) as exc:
                receipt.metadata_json = {
                    **metadata,
                    "historical_preview": {
                        "status": "INCOMPLETE",
                        "parse_status": "ERROR",
                        "canonical": {},
                        "missing_fields": [],
                        "conflicting_fields": [],
                        "source_verification": metadata["source_verification"],
                        "error": str(exc)[:255],
                    },
                }
                staged += 1
            session.flush()

        receipts = session.execute(
            select(HistoricalForwardReceipt).where(HistoricalForwardReceipt.batch_id == batch.id)
        ).scalars().all()
        accepted = sum(
            1
            for receipt in receipts
            if receipt.validation_status == "STAGED"
            and (receipt.metadata_json or {}).get("historical_preview", {}).get("status") in {"SUCCESS", "INCOMPLETE", "CONFLICT"}
        )
        rejected = sum(1 for receipt in receipts if receipt.validation_status in {"DUPLICATE", "REJECTED"})
        batch.total_records = len(receipts)
        batch.accepted_records = accepted
        batch.rejected_records = rejected
        manifest = [
            {
                "item_key": (receipt.metadata_json or {}).get("item_key"),
                "content_hash": receipt.content_hash,
                "source_chat_id": receipt.source_chat_id,
                "source_message_id": receipt.source_message_id,
                "source_message_revision": receipt.source_message_revision,
            }
            for receipt in receipts
        ]
        batch.manifest_hash = hashlib.sha256(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        batch.status = "REVIEW_REQUIRED" if accepted else "REJECTED"
        processed_count = len(receipts)
        changed_count = staged
        result_status = "NO_CHANGE" if changed_count == 0 else "PARTIAL_CHANGE" if rejected else "CHANGED"
        batch.metadata_json = {
            **(batch.metadata_json or {}),
            "intake_status": batch.status,
            "item_count_staged": staged,
            "item_count_rejected": rejected,
            "processed_count": processed_count,
            "changed_count": changed_count,
            "result_status": result_status,
            "batch_summary": {
                "partial": bool(is_partial),
                "has_incomplete": any(
                    (receipt.metadata_json or {}).get("historical_preview", {}).get("status") == "INCOMPLETE"
                    for receipt in receipts
                ),
                "has_conflict": any(
                    (receipt.metadata_json or {}).get("historical_preview", {}).get("status") == "CONFLICT"
                    for receipt in receipts
                ),
            },
        }
        session.flush()
        return self._batch_response(session, batch)

    def batch_report(self, session: Session, *, batch_id: int, requested_by_user_id: int) -> dict[str, Any]:
        batch = session.get(HistoricalImportBatch, batch_id)
        if batch is None or batch.requested_by_user_id != requested_by_user_id:
            raise HistoricalWebIntakeError("Historical batch not found")
        evidence = session.execute(select(HistoricalSignalEvidence).where(HistoricalSignalEvidence.batch_id == batch_id)).scalars().all()
        evidence_ids = [row.id for row in evidence]
        signals = session.execute(select(HistoricalSignal).where(HistoricalSignal.evidence_id.in_(evidence_ids))).scalars().all() if evidence_ids else []
        signal_ids = [row.id for row in signals]
        events = session.execute(select(HistoricalSignalEvent).where(HistoricalSignalEvent.signal_id.in_(signal_ids))).scalars().all() if signal_ids else []
        verified_events = [event for event in events if event.replay_status == "VERIFIED"]
        return {
            "ok": True,
            "report": {
                "batch_id": batch.id,
                "batch_ref": batch.batch_ref,
                "status": batch.status,
                "source_kind": batch.source_kind,
                "counts": {
                    "input_records": batch.total_records,
                    "accepted_records": batch.accepted_records,
                    "rejected_records": batch.rejected_records,
                    "processed_count": self._result_contract(batch)["processed_count"],
                    "changed_count": self._result_contract(batch)["changed_count"],
                    "result_status": self._result_contract(batch)["result_status"],
                    "evidence_records": len(evidence),
                    "historical_signals": len(signals),
                    "replay_events": len(events),
                    "verified_replay_events": len(verified_events),
                },
                "readiness": {
                    "owner_review_approved": (batch.metadata_json or {}).get("owner_review", {}).get("approved") is True,
                    "evidence_ingested": batch.status in {"EVIDENCE_INGESTED", "REPLAY_READY", "REPLAYED"},
                    "replay_available": bool(signals),
                    "commercial_enabled": False,
                },
                "signals": [
                    {
                        "public_ref": signal.public_ref,
                        "asset": signal.asset,
                        "side": signal.side,
                        "status": signal.status,
                        "confidence_score": str(signal.confidence_score),
                        "trust_tier": signal.trust_tier,
                        "eligible_for_ranking": bool(signal.eligible_for_ranking),
                        "events": sum(event.signal_id == signal.id for event in events),
                        "verified_events": sum(event.signal_id == signal.id and event.replay_status == "VERIFIED" for event in events),
                    }
                    for signal in signals
                ],
                "next_action": (
                    "OWNER_REVIEW" if batch.status in {"DRY_RUN", "REVIEW_REQUIRED"} else
                    "EVIDENCE_INGESTION" if batch.status == "VALIDATED" else
                    "G5_DRAFT_REVIEW" if batch.status == "EVIDENCE_INGESTED" and not signals else
                    "REPLAY_REVIEW" if signals and not verified_events else
                    "REPORT_READY"
                ),
            },
        }

    def list_batches(self, session: Session, *, requested_by_user_id: int, limit: int = 25) -> dict[str, Any]:
        if not requested_by_user_id:
            raise HistoricalWebIntakeError("requested_by_user_id is required")
        safe_limit = max(1, min(int(limit), 100))
        batches = session.execute(
            select(HistoricalImportBatch)
            .where(HistoricalImportBatch.requested_by_user_id == requested_by_user_id)
            .order_by(HistoricalImportBatch.created_at.desc())
            .limit(safe_limit)
        ).scalars().all()
        return {
            "ok": True,
            "batches": [self._batch_response(session, batch)["batch"] for batch in batches],
        }

    def get_batch(self, session: Session, *, batch_id: int, requested_by_user_id: int) -> dict[str, Any]:
        batch = session.get(HistoricalImportBatch, batch_id)
        if batch is None or batch.requested_by_user_id != requested_by_user_id:
            raise HistoricalWebIntakeError("Historical batch not found")
        return self._batch_response(session, batch)
