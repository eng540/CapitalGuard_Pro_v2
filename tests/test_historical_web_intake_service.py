from datetime import datetime, timezone

from capitalguard.application.services.historical_web_intake_service import HistoricalWebIntakeService
from capitalguard.application.services.historical_signal_service import HistoricalSignalService
from capitalguard.domain.entities import UserType
from sqlalchemy import select

from capitalguard.infrastructure.db.repository import UserRepository
from capitalguard.infrastructure.db.models import HistoricalForwardReceipt, HistoricalImportBatch, HistoricalRecommendationDraft


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


def test_web_intake_inspector_recovers_materialization_from_historical_draft(db_session):
    user = UserRepository(db_session).find_or_create(
        telegram_id=940003,
        user_type=UserType.ANALYST,
        first_name="Telegram Inspector Analyst",
    )
    result = HistoricalWebIntakeService().create_batch(
        db_session,
        requested_by_user_id=user.id,
        source_kind="TELEGRAM_EXPORT",
        input_mode="TELEGRAM_EXPORT",
        items=[
            {
                "item_key": "telegram-77",
                "raw_text": "#BTCUSDT LONG\nEntry: 100\nSL: 95\nTP1: 105",
                "source_chat_id": -1001234567890,
                "source_message_id": 77,
                "source_message_timestamp": "2025-01-01T10:00:00+00:00",
                "source_origin_type": "TELEGRAM_EXPORT",
            }
        ],
    )
    batch_id = result["batch"]["id"]
    receipt_id = result["batch"]["items"][0]["id"]
    receipt = db_session.get(HistoricalForwardReceipt, receipt_id)
    assert receipt is not None
    metadata = dict(receipt.metadata_json or {})
    metadata.pop("historical_preview", None)
    receipt.metadata_json = metadata
    db_session.flush()

    loaded = HistoricalWebIntakeService().get_batch(
        db_session,
        batch_id=batch_id,
        requested_by_user_id=user.id,
    )
    item = loaded["batch"]["items"][0]
    assert item["order"] == 1
    assert item["item_key"] == "telegram-77"
    assert item["parse_status"] == "MATERIALIZED"
    assert item["semantic_status"] in {"SUCCESS", "INCOMPLETE", "CONFLICT"}
    assert item["semantic_status"] != "NOT_PROCESSED"
    assert item["canonical"].get("asset") == "BTCUSDT"
    assert item["source_verification"] == "VERIFIED_PROVENANCE"


def test_web_historical_intake_returns_explicit_partial_change_contract(db_session):
    user = UserRepository(db_session).find_or_create(
        telegram_id=940004,
        user_type=UserType.ANALYST,
        first_name="Zero Result Contract Analyst",
    )
    result = HistoricalWebIntakeService().create_batch(
        db_session,
        requested_by_user_id=user.id,
        source_kind="MANUAL_ADMIN_IMPORT",
        input_mode="PASTE",
        items=[
            {"item_key": "accepted", "raw_text": "#BTCUSDT LONG Entry 100 SL 95 TP1 105"},
            {"item_key": "duplicate", "raw_text": "#BTCUSDT LONG Entry 100 SL 95 TP1 105"},
        ],
    )

    batch = result["batch"]
    assert batch["processed_count"] == 2
    assert batch["changed_count"] == 1
    assert batch["result_status"] == "PARTIAL_CHANGE"
    report = HistoricalWebIntakeService().batch_report(
        db_session,
        batch_id=batch["id"],
        requested_by_user_id=user.id,
    )
    assert report["report"]["counts"]["processed_count"] == 2
    assert report["report"]["counts"]["changed_count"] == 1
    assert report["report"]["counts"]["result_status"] == "PARTIAL_CHANGE"


def test_web_historical_intake_returns_explicit_no_change_contract_when_all_records_are_filtered(db_session):
    user = UserRepository(db_session).find_or_create(
        telegram_id=940005,
        user_type=UserType.ANALYST,
        first_name="No Change Analyst",
    )
    result = HistoricalWebIntakeService().create_batch(
        db_session,
        requested_by_user_id=user.id,
        source_kind="MANUAL_ADMIN_IMPORT",
        input_mode="PASTE",
        items=[None],
    )

    batch = result["batch"]
    assert batch["status"] == "REJECTED"
    assert batch["processed_count"] == 0
    assert batch["changed_count"] == 0
    assert batch["result_status"] == "NO_CHANGE"
    loaded = HistoricalWebIntakeService().get_batch(
        db_session,
        batch_id=batch["id"],
        requested_by_user_id=user.id,
    )
    assert loaded["batch"]["result_status"] == "NO_CHANGE"


def test_web_historical_report_exposes_core_lifecycle_and_ordered_timeline(db_session):
    user = UserRepository(db_session).find_or_create(
        telegram_id=940006,
        user_type=UserType.ANALYST,
        first_name="Timeline Analyst",
    )
    batch_result = HistoricalWebIntakeService().create_batch(
        db_session,
        requested_by_user_id=user.id,
        source_kind="TELEGRAM_EXPORT",
        input_mode="TELEGRAM_EXPORT",
        items=[
            {
                "item_key": "timeline-1",
                "raw_text": "#BTCUSDT LONG Entry 100 SL 90 TP1 110 TP2 120",
                "source_chat_id": -100123,
                "source_message_id": 900,
                "source_message_timestamp": "2025-01-01T10:00:00+00:00",
            }
        ],
    )
    batch_id = batch_result["batch"]["id"]
    db_session.get(HistoricalImportBatch, batch_id).status = "VALIDATED"
    historical = HistoricalSignalService()
    evidence = historical.ingest_evidence(
        db_session,
        source_kind="TELEGRAM_EXPORT",
        telegram_channel_id=-100123,
        telegram_message_id=900,
        message_timestamp=datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc),
        raw_text="#BTCUSDT LONG Entry 100 SL 90 TP1 110 TP2 120",
        batch_id=batch_id,
    )
    signal = historical.create_signal(
        db_session,
        evidence_id=evidence.id,
        decision_timestamp=datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc),
        asset="BTCUSDT",
        side="LONG",
        entry="100",
        stop_loss="90",
        targets=[{"price": "110"}, {"price": "120"}],
        market="Futures",
        public_ref="HIST-TIMELINE-0001",
    )
    historical.record_event(
        db_session,
        signal_id=signal.id,
        event_type="ACTIVATED",
        event_timestamp=datetime(2025, 1, 1, 10, 1, tzinfo=timezone.utc),
        market_as_of=datetime(2025, 1, 1, 10, 1, tzinfo=timezone.utc),
        data_source="BINANCE_FUTURES",
        replay_status="UNVERIFIED",
        dedup_key="timeline:activated",
    )
    historical.record_event(
        db_session,
        signal_id=signal.id,
        event_type="TP1",
        event_timestamp=datetime(2025, 1, 1, 10, 2, tzinfo=timezone.utc),
        market_as_of=datetime(2025, 1, 1, 10, 2, tzinfo=timezone.utc),
        data_source="BINANCE_FUTURES",
        price="110",
        replay_status="AMBIGUOUS",
        dedup_key="timeline:tp1",
    )

    report = HistoricalWebIntakeService().batch_report(
        db_session,
        batch_id=batch_id,
        requested_by_user_id=user.id,
    )
    view = report["report"]["signals"][0]
    assert view["public_ref"] == "HIST-TIMELINE-0001"
    assert view["lifecycle_status"] == "AMBIGUOUS"
    assert view["events"] == 2
    assert view["verified_events"] == 0
    assert view["last_event"] == "TP1"
    assert [event["event_type"] for event in view["timeline"]] == ["ACTIVATED", "TP1"]
    assert view["timeline"][1]["replay_status"] == "AMBIGUOUS"
    assert view["timeline"][1]["price"] == "110.00000000"


def test_web_historical_item_correction_is_user_scoped_idempotent_and_core_backed(db_session):
    user = UserRepository(db_session).find_or_create(
        telegram_id=940007,
        user_type=UserType.ANALYST,
        first_name="Correction Analyst",
    )
    result = HistoricalWebIntakeService().create_batch(
        db_session,
        requested_by_user_id=user.id,
        source_kind="MANUAL_ADMIN_IMPORT",
        input_mode="PASTE",
        items=[{"item_key": "needs-correction", "raw_text": "BTCUSDT LONG Entry 100"}],
    )
    batch_id = result["batch"]["id"]
    item_id = result["batch"]["items"][0]["id"]

    corrected = HistoricalWebIntakeService().correct_item(
        db_session,
        batch_id=batch_id,
        item_id=item_id,
        requested_by_user_id=user.id,
        fields={
            "asset": "BTCUSDT",
            "side": "LONG",
            "entry": 100,
            "stop_loss": 90,
            "market": "FUTURES",
            "targets": [{"price": 110, "percentage": 100}],
        },
        idempotency_key="correction-key-940007",
    )
    item = corrected["batch"]["items"][0]
    assert item["semantic_status"] == "SUCCESS"
    assert item["canonical"]["asset"] == "BTCUSDT"
    assert item["canonical"]["stop_loss"] == "90"
    assert item["canonical"]["targets"][0]["price"] == "110"

    receipt = db_session.get(HistoricalForwardReceipt, item_id)
    assert receipt is not None
    draft = db_session.execute(
        select(HistoricalRecommendationDraft).where(HistoricalRecommendationDraft.revision_id == receipt.id)
    ).scalars().first()
    assert (receipt.metadata_json or {}).get("last_correction", {}).get("actor_type") == "USER_CORRECTION"
    assert draft is not None
    assert draft.status == "ACCEPTED"
    assert draft.reviewed_by_user_id is None
    assert (draft.override_json or {}).get("actor_type") == "USER_CORRECTION"

    repeated = HistoricalWebIntakeService().correct_item(
        db_session,
        batch_id=batch_id,
        item_id=item_id,
        requested_by_user_id=user.id,
        fields={"entry": 101},
        idempotency_key="correction-key-940007",
    )
    assert repeated["batch"]["items"][0]["canonical"]["entry"] == "100"
