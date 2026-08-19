from datetime import datetime, timezone

from capitalguard.application.services.historical_manifest_service import HistoricalManifestService


def _record(message_id=1, timestamp="2026-01-01T12:00:00+00:00", text="BTCUSDT LONG"):
    return {
        "telegram_channel_id": -100123,
        "telegram_message_id": message_id,
        "message_revision": 0,
        "message_timestamp": timestamp,
        "raw_text": text,
    }


def test_manifest_dry_run_accepts_valid_records_and_hash_is_stable():
    service = HistoricalManifestService()
    payload = {"source_kind": "TELEGRAM_EXPORT", "records": [_record()]}
    report = service.validate(payload, now=datetime(2026, 1, 2, tzinfo=timezone.utc))
    same_report = service.validate(payload, now=datetime(2026, 1, 2, tzinfo=timezone.utc))

    assert report.is_valid is True
    assert report.accepted_records == 1
    assert report.rejected_records == 0
    assert report.manifest_hash == same_report.manifest_hash
    assert report.issues == ()


def test_manifest_rejects_duplicates_future_and_timezone_less_timestamps():
    service = HistoricalManifestService()
    payload = {
        "source_kind": "TELEGRAM_EXPORT",
        "records": [
            _record(message_id=1),
            _record(message_id=1),
            _record(message_id=2, timestamp="2026-01-03T12:00:00+00:00"),
            _record(message_id=3, timestamp="2026-01-01T12:00:00"),
        ],
    }
    report = service.validate(payload, now=datetime(2026, 1, 2, tzinfo=timezone.utc))
    codes = {issue.code for issue in report.issues}

    assert report.is_valid is False
    assert report.accepted_records == 1
    assert report.rejected_records == 3
    assert {"DUPLICATE_RECORD", "FUTURE_TIMESTAMP", "INVALID_TIMESTAMP"} <= codes


def test_manifest_rejects_unsupported_source_and_empty_records():
    report = HistoricalManifestService().validate({"source_kind": "UNKNOWN", "records": []})

    assert report.is_valid is False
    assert report.total_records == 0
    assert any(issue.code == "UNSUPPORTED_SOURCE_KIND" for issue in report.issues)


def test_import_orchestrator_registers_only_validated_batches(db_session):
    from capitalguard.application.services.historical_import_service import HistoricalImportService

    service = HistoricalImportService()
    valid_batch, valid_report = service.register_validated_batch(
        db_session,
        payload={"source_kind": "TELEGRAM_EXPORT", "records": [_record()]},
    )
    invalid_batch, invalid_report = service.register_validated_batch(
        db_session,
        payload={"source_kind": "TELEGRAM_EXPORT", "records": [_record(message_id=1), _record(message_id=1)]},
    )

    assert valid_report.is_valid is True
    assert valid_batch.status == "VALIDATED"
    assert invalid_report.is_valid is False
    assert invalid_batch.status == "REJECTED"
