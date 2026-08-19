"""Controlled historical import orchestration: dry-run first, persistence second."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import HistoricalImportBatch

from .historical_manifest_service import HistoricalManifestService, ManifestValidationReport
from .historical_signal_service import HistoricalSignalService


class HistoricalImportService:
    def __init__(self):
        self.manifest_service = HistoricalManifestService()
        self.signal_service = HistoricalSignalService()

    def dry_run(self, payload: dict[str, Any]) -> ManifestValidationReport:
        return self.manifest_service.validate(payload)

    def register_validated_batch(
        self,
        session: Session,
        *,
        payload: dict[str, Any],
        requested_by_user_id: int | None = None,
        channel_catalog_id: int | None = None,
    ) -> tuple[HistoricalImportBatch, ManifestValidationReport]:
        report = self.manifest_service.validate(payload)
        records = payload.get("records") if isinstance(payload.get("records"), list) else []
        batch = self.signal_service.create_import_batch(
            session,
            source_kind=report.source_kind,
            manifest=records,
            channel_catalog_id=channel_catalog_id,
            requested_by_user_id=requested_by_user_id,
            metadata={"dry_run_valid": report.is_valid, "issues": [issue.code for issue in report.issues]},
        )
        batch.accepted_records = report.accepted_records
        batch.rejected_records = report.rejected_records
        batch.status = "VALIDATED" if report.is_valid else "REJECTED"
        session.flush()
        return batch, report
