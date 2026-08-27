from capitalguard.interfaces.telegram.presentation_adapter import (
    CardAction,
    VisualCardState,
    build_batch_summary,
    build_card,
    build_single_result_card,
)


def test_build_card_is_user_facing_and_hides_operational_metadata():
    view = build_card(
        {
            "asset": "BTCUSDT",
            "side": "LONG",
            "entry": "64200",
            "stop_loss": "63100",
            "targets": [{"price": "65500", "percentage": 50}, {"price": "67000", "percentage": 50}],
        },
        temporal_route="HISTORICAL_CANDIDATE",
        source_timestamp="2026-08-26T10:00:00+00:00",
        source_title="Signals",
        allowed_actions=["IMPORT_HISTORICAL", "EDIT", "DISMISS"],
        callback_data_factory=lambda action: f"forward:{action}",
    )

    assert view.visual_state is VisualCardState.COMPLETE
    assert "BTCUSDT" in view.text
    assert "64200" in view.text
    assert "TP1" in view.text and "65500" in view.text
    assert "batch_id" not in view.text
    assert "claim_status" not in view.text
    assert "receipt_id" not in view.text
    assert "replay_gate" not in view.text
    assert view.actions == (
        CardAction.IMPORT_HISTORICAL.value,
        CardAction.EDIT.value,
        CardAction.DISMISS.value,
    )
    assert view.reply_markup is not None
    callback_values = [
        button.callback_data
        for row in view.reply_markup.inline_keyboard
        for button in row
    ]
    assert callback_values == [
        "forward:IMPORT_HISTORICAL",
        "forward:EDIT",
        "forward:DISMISS",
    ]


def test_build_card_preserves_incomplete_and_conflict_actions():
    view = build_card(
        {"asset": "ETHUSDT", "side": "SHORT", "entry": None, "stop_loss": "3500", "targets": []},
        temporal_route="REVISION_REVIEW",
        internal_status="INCOMPLETE",
        allowed_actions=["COMPLETE_DATA", "ACCEPT_TEXT", "ACCEPT_IMAGE", "MANUAL_ENTRY"],
    )

    assert view.visual_state is VisualCardState.INCOMPLETE
    assert "يحتاج استكمالًا بسيطًا" in view.text
    assert "تعارض" not in view.text
    assert view.actions == (
        CardAction.COMPLETE_DATA.value,
        CardAction.ACCEPT_TEXT.value,
        CardAction.ACCEPT_IMAGE.value,
        CardAction.MANUAL_ENTRY.value,
    )


def test_conflicting_provenance_forces_incomplete_without_choosing_a_source():
    view = build_card(
        {"asset": "SOLUSDT", "side": "LONG", "entry": "142", "stop_loss": "138", "targets": ["146"]},
        provenance={"conflict": True, "fields": {"entry": ["TEXT", "IMAGE"]}},
        allowed_actions=["ACCEPT_TEXT", "ACCEPT_IMAGE", "MANUAL_ENTRY"],
    )

    assert view.visual_state is VisualCardState.INCOMPLETE
    assert "توجد قيم متعارضة" in view.text
    assert "اعتماد النص" in [button.text for row in view.reply_markup.inline_keyboard for button in row]
    assert "اعتماد الصورة" in [button.text for row in view.reply_markup.inline_keyboard for button in row]


def test_unavailable_card_keeps_retry_and_does_not_offer_live_activation():
    view = build_card(
        {"asset": "BTCUSDT", "side": "LONG"},
        temporal_route="QUARANTINE",
        internal_status="PROVIDER_RETRYABLE",
        allowed_actions=["RETRY", "MANUAL_ENTRY", "ACCEPT_LIVE_REVIEW"],
    )

    assert view.visual_state is VisualCardState.UNAVAILABLE
    assert view.actions == (
        CardAction.RETRY.value,
        CardAction.MANUAL_ENTRY.value,
    )
    assert "تعذر تجهيز التوصية مؤقتًا" in view.text


def test_build_batch_summary_is_concise_and_hides_batch_identifiers():
    view = build_batch_summary(
        {
            "total_records": 80,
            "processed_records": 48,
            "accepted_records": 32,
            "partial_count": 12,
            "failed_records": 4,
            "duplicate_records": 2,
            "source_title": "VIP Signals",
        },
        allowed_actions=["IMPORT_HISTORICAL", "TRACK_ONLY", "DISMISS"],
    )

    assert "VIP Signals" in view.text
    assert "48" in view.text and "80" in view.text
    assert "مكتملة" in view.text
    assert "تحتاج استكمالًا:" in view.text
    assert "تعذر تجهيزها" in view.text
    assert "مكررة" in view.text
    assert "batch_id" not in view.text
    assert "receipt_id" not in view.text
    assert "replay_gate" not in view.text
    assert view.actions == (
        CardAction.IMPORT_HISTORICAL.value,
        CardAction.TRACK_ONLY.value,
        CardAction.DISMISS.value,
    )


def test_unknown_actions_are_not_rendered():
    view = build_card(
        {"asset": "BTCUSDT"},
        allowed_actions=["IMPORT_HISTORICAL", "UNKNOWN_INTERNAL_ACTION", "DUPLICATE"],
    )

    assert view.actions == (CardAction.IMPORT_HISTORICAL.value,)
    assert "UNKNOWN_INTERNAL_ACTION" not in view.text


def test_build_batch_summary_shows_extracted_values_without_internal_metadata():
    view = build_batch_summary(
        {"total_records": 1, "processed_records": 1, "complete_records": 1},
        extracted_items=[
            {
                "asset": "ETHUSDT",
                "side": "SHORT",
                "entry": "3500",
                "stop_loss": "3600",
                "targets": [{"price": "3400"}],
                "batch_id": 99,
                "replay_gate": "HOLD",
            }
        ],
    )

    assert "ETHUSDT" in view.text
    assert "SHORT" in view.text
    assert "3500" in view.text
    assert "TP1" in view.text and "3400" in view.text
    assert "batch_id" not in view.text
    assert "replay_gate" not in view.text


def test_build_single_result_card_shows_extraction_and_source_outcome_distinctly():
    view = build_single_result_card(
        {
            "asset": "BTCUSDT",
            "side": "LONG",
            "entry": "88246.80",
            "stop_loss": "87000",
            "targets": [{"price": "89000", "percentage": 20}, {"price": "90000", "percentage": 80}],
            "exit_price": "90000",
        },
        temporal_route="HISTORICAL_CANDIDATE",
        source_timestamp="2026-08-27T10:00:00+00:00",
        financial_outcome={"status": "REPORTED_ONLY", "reported_pnl_pct": "1.76"},
        allowed_actions=["TRACK_ONLY"],
    )

    assert "نتيجة ما عمله النظام" in view.text
    assert "BTCUSDT" in view.text
    assert "88246.80" in view.text
    assert "النتيجة الموجودة في الرسالة المصدر" in view.text
    assert "لم تُعتبر Replay موثقًا" in view.text
    assert "1.76" in view.text
    assert "batch_id" not in view.text
    assert "replay_gate" not in view.text


def test_build_single_result_card_labels_unverifiable_replay_truthfully():
    view = build_single_result_card(
        {"asset": "BTCUSDT", "side": "LONG", "entry": "100", "stop_loss": "90", "targets": [{"price": "110"}]},
        temporal_route="HISTORICAL_CANDIDATE",
        replay_result={
            "replay_status": "COMPLETED_UNVERIFIABLE",
            "event_count": 2,
            "last_event": "TP1",
            "lifecycle_status": "CLOSED_TARGETS",
            "replay_run_ref": "HIDDEN-REF",
        },
    )

    assert "اكتملت، لكن بيانات السوق غير قابلة للتحقق" in view.text
    assert "لا تُستخدم كنتيجة نهائية أو للترتيب" in view.text
    assert "COMPLETED_UNVERIFIABLE" in view.text
    assert "HIDDEN-REF" not in view.text


def test_build_single_result_card_shows_provider_failure_as_pending_not_verified():
    view = build_single_result_card(
        {"asset": "BTCUSDT", "side": "LONG", "entry": "100", "stop_loss": "90", "targets": [{"price": "110"}]},
        temporal_route="HISTORICAL_CANDIDATE",
        replay_result={
            "replay_status": "PROVIDER_UNAVAILABLE",
            "reason": "Historical market data could not be fetched; G5 evidence was preserved.",
        },
    )

    assert "لم تكتمل بعد؛ تم حفظ الاستخراج" in view.text
    assert "PROVIDER_UNAVAILABLE" not in view.text
    assert "بيانات السوق غير قابلة للتحقق" not in view.text


def test_build_single_result_card_is_still_visible_when_extraction_fails():
    view = build_single_result_card(
        {},
        temporal_route="QUARANTINE",
        source_title="Crypto source",
        source_timestamp="2026-08-27T15:00:00+00:00",
        internal_status="FAILED",
        allowed_actions=["RETRY", "MANUAL_ENTRY", "DISMISS"],
    )

    assert "تعذر تجهيز التوصية مؤقتًا" in view.text
    assert "الأصل" in view.text
    assert "إكمال البيانات" not in view.text
    assert view.actions == (CardAction.RETRY.value, CardAction.MANUAL_ENTRY.value, CardAction.DISMISS.value)


def test_complete_extraction_stays_visible_when_semantic_work_is_deferred():
    view = build_single_result_card(
        {
            "asset": "BTCUSDT",
            "side": "LONG",
            "entry": "79625.20",
            "stop_loss": "79000",
            "targets": [{"price": "79800"}, {"price": "80000"}],
        },
        temporal_route="HISTORICAL_CANDIDATE",
        internal_status="SEMANTIC_REVIEW_REQUIRED",
    )

    assert view.visual_state is VisualCardState.COMPLETE
    assert "تم استخراج التوصية" in view.text
    assert "يحتاج استكمالًا بسيطًا" not in view.text
    assert "SEMANTIC_REVIEW_REQUIRED" not in view.text
    assert "BTCUSDT" in view.text and "79625.20" in view.text



def test_deferred_replay_explains_result_without_exposing_internal_block_reason():
    view = build_single_result_card(
        {"asset": "BTCUSDT", "side": "LONG", "entry": "100", "stop_loss": "90", "targets": [{"price": "110"}]},
        temporal_route="HISTORICAL_CANDIDATE",
        internal_status="SEMANTIC_REVIEW_REQUIRED",
        replay_result={"replay_status": "BLOCKED", "reason": "SEMANTIC_REVIEW_REQUIRED"},
    )

    assert "المحاكاة التاريخية مؤجلة؛ نتيجة الاستخراج جاهزة." in view.text
    assert "SEMANTIC_REVIEW_REQUIRED" not in view.text
    assert "AUTO_PROGRESS_BLOCKED" not in view.text
