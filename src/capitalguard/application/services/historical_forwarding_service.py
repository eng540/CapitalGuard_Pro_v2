from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import HistoricalForwardReceipt, HistoricalImportBatch

from .historical_message_foundation_service import HistoricalMessageFoundationService
from .historical_signal_service import HistoricalSignalService, HistoricalSignalValidationError


@dataclass(frozen=True)
class ForwardedMessageInput:
    receiver_chat_id: int
    receiver_message_id: int
    forwarding_user_id: int | None
    source_chat_id: int | None
    source_message_id: int | None
    source_origin_type: str
    source_message_timestamp: datetime | None
    raw_text: str | None
    source_message_revision: int = 0
    source_edit_date: datetime | None = None
    source_reply_to_message_id: int | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ForwardingPreview:
    batch_id: int
    total_records: int
    accepted_records: int
    rejected_records: int
    duplicate_records: int
    hidden_origin_records: int
    manifest: dict[str, Any]


class HistoricalForwardingService:
    """Stages user-forwarded Telegram messages for historical review only."""

    SOURCE_KIND = "TELEGRAM_FORWARD"
    VALID_ORIGIN_TYPES = {"CHANNEL", "MESSAGE_ORIGIN_CHANNEL"}

    def __init__(
        self,
        signal_service: HistoricalSignalService | None = None,
        message_foundation_service: HistoricalMessageFoundationService | None = None,
    ):
        self.signal_service = signal_service or HistoricalSignalService()
        self.message_foundation_service = message_foundation_service or HistoricalMessageFoundationService()

    @staticmethod
    def _normalize_chat_id(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def canonical_order_key(message: Any) -> tuple[int, datetime, int, int]:
        """Return deterministic source ordering for receipts and manifest records."""
        source_chat_id = HistoricalForwardingService._normalize_chat_id(
            message.get("source_chat_id") if isinstance(message, Mapping) else getattr(message, "source_chat_id", None)
        )
        source_timestamp = (
            message.get("source_message_timestamp")
            if isinstance(message, Mapping)
            else getattr(message, "source_message_timestamp", None)
        )
        source_message_id = HistoricalForwardingService._normalize_chat_id(
            message.get("source_message_id") if isinstance(message, Mapping) else getattr(message, "source_message_id", None)
        )
        revision = (
            message.get("source_message_revision", 0)
            if isinstance(message, Mapping)
            else getattr(message, "source_message_revision", 0)
        )
        if source_timestamp is None and isinstance(message, Mapping):
            source_timestamp = message.get("message_timestamp")
        if source_timestamp is None:
            source_timestamp = datetime.min.replace(tzinfo=timezone.utc)
        elif source_timestamp.tzinfo is None:
            source_timestamp = source_timestamp.replace(tzinfo=timezone.utc)
        else:
            source_timestamp = source_timestamp.astimezone(timezone.utc)
        try:
            revision_value = max(0, int(revision or 0))
        except (TypeError, ValueError):
            revision_value = 0
        return (
            source_chat_id if source_chat_id is not None else -(2**63),
            source_timestamp,
            source_message_id if source_message_id is not None else -1,
            revision_value,
        )

    @classmethod
    def ordered_receipts(cls, receipts: Iterable[HistoricalForwardReceipt]) -> list[HistoricalForwardReceipt]:
        """Order receipts by source facts, never by receiver arrival order."""
        return sorted(list(receipts), key=cls.canonical_order_key)

    @classmethod
    def _timeline_annotations(cls, receipts: Iterable[HistoricalForwardReceipt]) -> dict[int, dict[str, Any]]:
        """Annotate reply relationships without guessing unrelated messages together."""
        ordered = cls.ordered_receipts(receipts)
        source_ids = {
            receipt.source_message_id
            for receipt in ordered
            if receipt.source_message_id is not None
        }
        annotations: dict[int, dict[str, Any]] = {}
        for receipt in ordered:
            parent_id = receipt.source_reply_to_message_id
            if parent_id is not None and parent_id in source_ids:
                annotations[receipt.id] = {
                    "timeline_role": "CHILD_UPDATE",
                    "timeline_parent_message_id": parent_id,
                    "timeline_link_status": "EXPLICIT_REPLY",
                }
            elif parent_id is not None:
                annotations[receipt.id] = {
                    "timeline_role": "UNRESOLVED_CHILD",
                    "timeline_parent_message_id": parent_id,
                    "timeline_link_status": "PENDING_REVIEW",
                }
            else:
                annotations[receipt.id] = {
                    "timeline_role": "ROOT_CANDIDATE",
                    "timeline_parent_message_id": None,
                    "timeline_link_status": "UNLINKED",
                }
        return annotations

    @staticmethod
    def _source_content_hash(raw_text: str | None, metadata: dict[str, Any]) -> str:
        """Hash the raw semantic input, including media identity when present."""
        import hashlib
        import json

        media = metadata.get("media") or {}
        payload = {
            "raw_text": raw_text or "",
            "media": {
                "file_id": media.get("file_id"),
                "media_unique_id": media.get("media_unique_id"),
                "media_type": media.get("media_type"),
            },
        }
        normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_positive(value: int, field: str) -> None:
        if not isinstance(value, int) or value <= 0:
            raise HistoricalSignalValidationError(f"{field} must be a positive integer")

    def start_batch(
        self,
        session: Session,
        *,
        channel_catalog_id: int,
        requested_by_user_id: int,
        expected_source_chat_id: int,
        mode: str = "BATCH",
        max_records: int = 500,
    ) -> HistoricalImportBatch:
        self._validate_positive(channel_catalog_id, "channel_catalog_id")
        self._validate_positive(requested_by_user_id, "requested_by_user_id")
        expected_source_chat_id = self._normalize_chat_id(expected_source_chat_id)
        if expected_source_chat_id in (None, 0):
            raise HistoricalSignalValidationError("expected_source_chat_id must be a non-zero integer")
        if mode not in {"SINGLE", "BATCH"}:
            raise HistoricalSignalValidationError("Unsupported forwarding mode")
        if not 1 <= max_records <= 5000:
            raise HistoricalSignalValidationError("max_records must be between 1 and 5000")
        batch = self.signal_service.create_import_batch(
            session,
            source_kind=self.SOURCE_KIND,
            manifest=[],
            channel_catalog_id=channel_catalog_id,
            requested_by_user_id=requested_by_user_id,
            metadata={
                "mode": mode,
                "expected_source_chat_id": expected_source_chat_id,
                "max_records": max_records,
                "intake_status": "STAGING",
            },
        )
        batch.status = "STAGING"
        session.flush()
        return batch

    def stage_message(
        self,
        session: Session,
        *,
        batch_id: int,
        message: ForwardedMessageInput,
    ) -> HistoricalForwardReceipt:
        batch = session.get(HistoricalImportBatch, batch_id)
        if batch is None:
            raise HistoricalSignalValidationError("Forwarding batch does not exist")
        if batch.status != "STAGING":
            raise HistoricalSignalValidationError("Forwarding batch is not open for staging")
        self._validate_positive(message.receiver_chat_id, "receiver_chat_id")
        self._validate_positive(message.receiver_message_id, "receiver_message_id")
        metadata = dict(message.metadata or {})
        expected_source_chat_id = self._normalize_chat_id(
            (batch.metadata_json or {}).get("expected_source_chat_id")
        )
        source_chat_id = self._normalize_chat_id(message.source_chat_id)
        max_records = int((batch.metadata_json or {}).get("max_records") or 500)

        existing_receiver = session.execute(
            select(HistoricalForwardReceipt).where(
                HistoricalForwardReceipt.receiver_chat_id == message.receiver_chat_id,
                HistoricalForwardReceipt.receiver_message_id == message.receiver_message_id,
            )
        ).scalar_one_or_none()
        if existing_receiver is not None:
            return existing_receiver

        source_timestamp = self._utc(message.source_message_timestamp)
        edit_date = self._utc(message.source_edit_date)
        source_revision = max(0, int(message.source_message_revision or 0))
        content_hash = self._source_content_hash(message.raw_text, metadata)
        rejection_reason = None
        validation_status = "STAGED"
        origin_type = str(message.source_origin_type or "UNKNOWN").upper()
        if len([item for item in session.execute(
            select(HistoricalForwardReceipt).where(HistoricalForwardReceipt.batch_id == batch_id)
        ).scalars().all()]) >= max_records:
            raise HistoricalSignalValidationError("Forwarding batch max_records exceeded")
        if origin_type not in self.VALID_ORIGIN_TYPES or source_chat_id is None or message.source_message_id is None:
            validation_status = "REJECTED_ORIGIN"
            rejection_reason = "Missing or hidden channel origin"
        elif expected_source_chat_id is None or source_chat_id != expected_source_chat_id:
            validation_status = "REJECTED_CHANNEL"
            rejection_reason = "Forwarded source channel is not allow-listed"
        elif source_timestamp is None:
            validation_status = "REJECTED_TIMESTAMP"
            rejection_reason = "Source message timestamp is required"
        elif source_timestamp > datetime.now(timezone.utc):
            validation_status = "REJECTED_TIMESTAMP"
            rejection_reason = "Source message timestamp is in the future"

        existing_source = None
        if source_chat_id is not None and message.source_message_id is not None:
            existing_source = session.execute(
                select(HistoricalForwardReceipt).where(
                    HistoricalForwardReceipt.batch_id == batch_id,
                    HistoricalForwardReceipt.source_chat_id == source_chat_id,
                    HistoricalForwardReceipt.source_message_id == message.source_message_id,
                    HistoricalForwardReceipt.source_message_revision == source_revision,
                )
            ).scalar_one_or_none()
        if existing_source is not None:
            return existing_source

        receipt = HistoricalForwardReceipt(
            batch_id=batch_id,
            forwarding_user_id=message.forwarding_user_id,
            receiver_chat_id=message.receiver_chat_id,
            receiver_message_id=message.receiver_message_id,
            source_chat_id=source_chat_id,
            source_message_id=message.source_message_id,
            source_message_revision=source_revision,
            source_origin_type=origin_type,
            source_message_timestamp=source_timestamp,
            source_edit_date=edit_date,
            source_reply_to_message_id=message.source_reply_to_message_id,
            raw_text=message.raw_text,
            content_hash=content_hash,
            validation_status=validation_status,
            rejection_reason=rejection_reason,
            metadata_json=metadata,
        )
        session.add(receipt)
        session.flush()
        if receipt.validation_status == "STAGED":
            self.message_foundation_service.record_receipt(session, receipt=receipt)
        return receipt

    def preview_batch(self, session: Session, *, batch_id: int) -> ForwardingPreview:
        batch = session.get(HistoricalImportBatch, batch_id)
        if batch is None:
            raise HistoricalSignalValidationError("Forwarding batch does not exist")
        if batch.status not in {"STAGING", "DRY_RUN"}:
            raise HistoricalSignalValidationError("Only a staged batch can be previewed")
        receipts = list(session.execute(
            select(HistoricalForwardReceipt).where(HistoricalForwardReceipt.batch_id == batch_id)
        ).scalars().all())
        accepted = [receipt for receipt in receipts if receipt.validation_status == "STAGED"]
        rejected = [receipt for receipt in receipts if receipt.validation_status.startswith("REJECTED")]
        hidden = [receipt for receipt in receipts if receipt.validation_status == "REJECTED_ORIGIN"]
        duplicates = [receipt for receipt in receipts if receipt.validation_status == "DUPLICATE"]
        ordered_accepted = self.ordered_receipts(accepted)
        timeline_annotations = self._timeline_annotations(ordered_accepted)
        manifest = {
            "source_kind": self.SOURCE_KIND,
            "ordering": "source_chat_id,source_message_timestamp,source_message_id,source_message_revision",
            "records": [
                {
                    "telegram_channel_id": receipt.source_chat_id,
                    "telegram_message_id": receipt.source_message_id,
                    "message_revision": receipt.source_message_revision,
                    "message_timestamp": receipt.source_message_timestamp.isoformat() if receipt.source_message_timestamp else None,
                    "raw_text": receipt.raw_text,
                    "source_uri": f"telegram-forward://{receipt.receiver_chat_id}/{receipt.receiver_message_id}",
                    "metadata": {
                        **(receipt.metadata_json or {}),
                        "receiver_chat_id": receipt.receiver_chat_id,
                        "receiver_message_id": receipt.receiver_message_id,
                        "source_origin_type": receipt.source_origin_type,
                        "source_edit_date": receipt.source_edit_date.isoformat() if receipt.source_edit_date else None,
                        "source_reply_to_message_id": receipt.source_reply_to_message_id,
                        "forwarding_receipt_id": receipt.id,
                        **timeline_annotations.get(receipt.id, {}),
                    },
                }
                for receipt in ordered_accepted
            ],
        }
        batch.total_records = len(receipts)
        batch.accepted_records = len(accepted)
        batch.rejected_records = len(rejected)
        batch.status = "DRY_RUN"
        batch.metadata_json = {**(batch.metadata_json or {}), "intake_status": "DRY_RUN"}
        session.flush()
        return ForwardingPreview(
            batch_id=batch_id,
            total_records=len(receipts),
            accepted_records=len(accepted),
            rejected_records=len(rejected),
            duplicate_records=len(duplicates),
            hidden_origin_records=len(hidden),
            manifest=manifest,
        )

    def apply_preview_decision(
        self,
        session: Session,
        *,
        batch_id: int,
        requested_by_user_id: int,
        action: str,
    ) -> HistoricalImportBatch:
        """Apply a human decision to a dry-run historical batch without creating live entities."""
        batch = session.get(HistoricalImportBatch, batch_id)
        if batch is None:
            raise HistoricalSignalValidationError("Forwarding batch does not exist")
        if batch.status != "DRY_RUN":
            raise HistoricalSignalValidationError("Historical preview is no longer awaiting a decision")
        if not requested_by_user_id or batch.requested_by_user_id != requested_by_user_id:
            raise HistoricalSignalValidationError("Only the batch requester can choose a historical preview action")

        normalized = action.strip().upper()
        metadata = dict(batch.metadata_json or {})
        review_modes = ((metadata.get("parser_preview") or {}).get("review_actions_by_mode") or {}).values()
        allowed_sets = [set(str(item).upper() for item in actions) for actions in review_modes]
        allowed_actions = set.intersection(*allowed_sets) if allowed_sets else {"DISMISS"}
        if normalized not in allowed_actions:
            raise HistoricalSignalValidationError("Historical preview action is not allowed for this batch")

        now = datetime.now(timezone.utc).isoformat()
        metadata["preview_decision"] = {
            "action": normalized,
            "requested_by_user_id": requested_by_user_id,
            "decided_at": now,
        }
        if normalized == "IMPORT_HISTORICAL":
            if batch.accepted_records <= 0:
                raise HistoricalSignalValidationError("Cannot request historical review without accepted records")
            batch.status = "REVIEW_REQUIRED"
            metadata["intake_status"] = "REVIEW_REQUIRED"
        elif normalized == "TRACK_ONLY":
            batch.status = "TRACK_ONLY"
            metadata["intake_status"] = "TRACK_ONLY"
        elif normalized == "DISMISS":
            batch.status = "DISMISSED"
            metadata["intake_status"] = "DISMISSED"
        else:
            raise HistoricalSignalValidationError("Unsupported historical preview action")
        batch.metadata_json = metadata
        session.flush()
        return batch

    def validate_batch(self, session: Session, *, batch_id: int, owner_note: str) -> HistoricalImportBatch:
        preview = self.preview_batch(session, batch_id=batch_id)
        if not owner_note.strip():
            raise HistoricalSignalValidationError("Owner approval note is required")
        batch = self.signal_service.validate_import_batch(
            session,
            batch_id=batch_id,
            accepted_records=preview.accepted_records,
            rejected_records=preview.rejected_records,
        )
        batch.metadata_json = {**(batch.metadata_json or {}), "owner_approval_note": owner_note.strip()}
        session.flush()
        return batch

    def ingest_validated_batch(self, session: Session, *, batch_id: int) -> list[Any]:
        batch = session.get(HistoricalImportBatch, batch_id)
        if batch is None or batch.status != "VALIDATED":
            raise HistoricalSignalValidationError("Only VALIDATED forwarding batches can ingest evidence")
        receipts = list(session.execute(
            select(HistoricalForwardReceipt).where(
                HistoricalForwardReceipt.batch_id == batch_id,
                HistoricalForwardReceipt.validation_status == "STAGED",
            )
        ).scalars().all())
        evidence = []
        for receipt in receipts:
            item = self.signal_service.ingest_evidence(
                session,
                batch_id=batch.id,
                source_kind=self.SOURCE_KIND,
                channel_catalog_id=batch.channel_catalog_id,
                telegram_channel_id=receipt.source_chat_id,
                telegram_message_id=receipt.source_message_id,
                message_revision=receipt.source_message_revision,
                message_timestamp=receipt.source_message_timestamp,
                raw_text=receipt.raw_text,
                source_uri=f"telegram-forward://{receipt.receiver_chat_id}/{receipt.receiver_message_id}",
                metadata=receipt.metadata_json,
            )
            receipt.evidence_id = item.id
            receipt.validation_status = "INGESTED"
            evidence.append(item)
        session.flush()
        return evidence
