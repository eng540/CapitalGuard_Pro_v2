from capitalguard.application.services.historical_web_intake_service import HistoricalWebIntakeService
from capitalguard.domain.entities import UserType
from capitalguard.infrastructure.db.repository import UserRepository


def test_web_historical_intake_supports_single_multiple_partial_batch_and_dedup(db_session):
    user = UserRepository(db_session).find_or_create(
        telegram_id=940001,
        user_type=UserType.ANALYST,
        first_name="Web Intake Analyst",
    )

    result = HistoricalWebIntakeService().create_batch(
        db_session,
        requested_by_user_id=user.id,
        source_kind="MANUAL_ADMIN_IMPORT",
        input_mode="PASTE",
        is_partial=True,
        batch_label="Manual multi-message sample",
        items=[
            {"item_key": "signal-1", "raw_text": "#BTCUSDT LONG\nEntry: 100\nSL: 95\nTP1: 105", "source_origin_type": "WEB_PASTE"},
            {"item_key": "update-1", "raw_text": "Update: move stop to entry 100", "source_origin_type": "WEB_PASTE"},
            {"item_key": "duplicate-1", "raw_text": "#BTCUSDT LONG\nEntry: 100\nSL: 95\nTP1: 105", "source_origin_type": "WEB_PASTE"},
        ],
    )

    batch = result["batch"]
    assert batch["status"] == "REVIEW_REQUIRED"
    assert batch["total_records"] == 3
    assert batch["accepted_records"] == 2
    assert batch["rejected_records"] == 1
    assert batch["metadata"]["is_partial"] is True
    assert len(batch["items"]) == 3
    assert batch["items"][0]["order"] == 1
    assert batch["items"][0]["source_verification"] == "UNVERIFIED"
    assert batch["items"][2]["status"] == "DUPLICATE"
    assert batch["items"][0]["semantic_status"] in {"SUCCESS", "INCOMPLETE"}

    loaded = HistoricalWebIntakeService().get_batch(
        db_session,
        batch_id=batch["id"],
        requested_by_user_id=user.id,
    )
    assert loaded["batch"]["id"] == batch["id"]
    assert loaded["batch"]["metadata"]["batch_summary"]["partial"] is True
    report = HistoricalWebIntakeService().batch_report(db_session, batch_id=batch["id"], requested_by_user_id=user.id)
    assert report["report"]["counts"]["input_records"] == 3
    assert report["report"]["counts"]["historical_signals"] == 0
    assert report["report"]["next_action"] == "OWNER_REVIEW"


def test_web_historical_intake_preserves_telegram_export_provenance(db_session):
    user = UserRepository(db_session).find_or_create(
        telegram_id=940002,
        user_type=UserType.ANALYST,
        first_name="Web Export Analyst",
    )

    result = HistoricalWebIntakeService().create_batch(
        db_session,
        requested_by_user_id=user.id,
        source_kind="TELEGRAM_EXPORT",
        input_mode="TELEGRAM_EXPORT",
        items=[
            {
                "item_key": "telegram-42",
                "raw_text": "#ETHUSDT SHORT\nEntry: 200\nSL: 210\nTP1: 190",
                "source_chat_id": -1001234567890,
                "source_message_id": 42,
                "source_message_timestamp": "2025-01-01T10:00:00+00:00",
                "source_origin_type": "TELEGRAM_EXPORT",
                "source_uri": "fixture://telegram-export.json#42",
            }
        ],
    )

    item = result["batch"]["items"][0]
    assert result["batch"]["status"] == "REVIEW_REQUIRED"
    assert item["source_verification"] == "VERIFIED_PROVENANCE"
    assert item["source_chat_id"] == -1001234567890
    assert item["source_message_id"] == 42
    assert item["semantic_status"] in {"SUCCESS", "INCOMPLETE", "CONFLICT"}
