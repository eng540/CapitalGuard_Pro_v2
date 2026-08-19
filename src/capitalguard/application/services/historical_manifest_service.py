"""Manifest schema and dry-run validation for controlled historical imports."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .historical_signal_service import HistoricalSignalService


@dataclass(frozen=True)
class ManifestIssue:
    index: int
    code: str
    message: str


@dataclass(frozen=True)
class ManifestValidationReport:
    source_kind: str
    total_records: int
    accepted_records: int
    rejected_records: int
    manifest_hash: str
    issues: tuple[ManifestIssue, ...]

    @property
    def is_valid(self) -> bool:
        return self.rejected_records == 0 and self.total_records > 0


class HistoricalManifestService:
    SUPPORTED_SOURCE_KINDS = frozenset(HistoricalSignalService.SOURCE_CONFIDENCE)

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _canonical_manifest(source_kind: str, records: list[dict[str, Any]]) -> str:
        return json.dumps(
            {"source_kind": source_kind, "records": records},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @staticmethod
    def _record_key(record: dict[str, Any], index: int) -> str:
        channel_id = record.get("telegram_channel_id") or "unknown"
        message_id = record.get("telegram_message_id")
        revision = record.get("message_revision", 0)
        if message_id is not None:
            return f"telegram:{channel_id}:{message_id}:r{revision}"
        raw = " ".join(str(record.get("raw_text") or "").split()).casefold()
        timestamp = record.get("message_timestamp") or f"index:{index}"
        content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return f"content:{channel_id}:{timestamp}:{content_hash}"

    def validate(self, payload: dict[str, Any], *, now: datetime | None = None) -> ManifestValidationReport:
        now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        source_kind = str(payload.get("source_kind") or "").strip().upper()
        raw_records = payload.get("records")
        records = raw_records if isinstance(raw_records, list) else []
        issues: list[ManifestIssue] = []
        seen: set[str] = set()
        accepted = 0

        if source_kind not in self.SUPPORTED_SOURCE_KINDS:
            issues.append(ManifestIssue(-1, "UNSUPPORTED_SOURCE_KIND", f"Unsupported source_kind: {source_kind or '<empty>'}"))

        for index, record in enumerate(records):
            if not isinstance(record, dict):
                issues.append(ManifestIssue(index, "RECORD_NOT_OBJECT", "Each manifest record must be an object"))
                continue
            record_issues: list[ManifestIssue] = []
            timestamp = self._parse_timestamp(record.get("message_timestamp"))
            if timestamp is None:
                record_issues.append(ManifestIssue(index, "INVALID_TIMESTAMP", "message_timestamp must be timezone-aware ISO-8601"))
            elif timestamp > now_utc:
                record_issues.append(ManifestIssue(index, "FUTURE_TIMESTAMP", "message_timestamp cannot be in the future"))
            if not str(record.get("raw_text") or "").strip():
                record_issues.append(ManifestIssue(index, "EMPTY_RAW_TEXT", "raw_text is required"))
            message_id = record.get("telegram_message_id")
            if message_id is not None and (not isinstance(message_id, int) or message_id <= 0):
                record_issues.append(ManifestIssue(index, "INVALID_MESSAGE_ID", "telegram_message_id must be a positive integer"))
            revision = record.get("message_revision", 0)
            if not isinstance(revision, int) or revision < 0:
                record_issues.append(ManifestIssue(index, "INVALID_REVISION", "message_revision must be a non-negative integer"))
            key = self._record_key(record, index)
            if key in seen:
                record_issues.append(ManifestIssue(index, "DUPLICATE_RECORD", "The record duplicates another manifest record"))
            if not record_issues:
                seen.add(key)
                accepted += 1
            issues.extend(record_issues)

        canonical = self._canonical_manifest(source_kind, records)
        return ManifestValidationReport(
            source_kind=source_kind,
            total_records=len(records),
            accepted_records=accepted,
            rejected_records=len(records) - accepted,
            manifest_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            issues=tuple(issues),
        )

    def load_json(self, path: str) -> dict[str, Any]:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("Manifest root must be a JSON object")
        return payload
