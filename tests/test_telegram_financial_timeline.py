from capitalguard.interfaces.telegram.presentation_adapter import build_single_result_card


def _candidate():
    return {
        "asset": "BTCUSDT",
        "side": "LONG",
        "market": "FUTURES",
        "entry": 98000,
        "stop_loss": 97000,
        "targets": [99000, 100000],
    }


def test_single_result_renders_persisted_event_timeline():
    view = build_single_result_card(
        _candidate(),
        temporal_route="HISTORICAL_CANDIDATE",
        replay_result={
            "replay_status": "COMPLETED",
            "lifecycle_state": "CLOSED_TARGETS",
            "net_pnl_pct": 2.04,
            "events_payload": [
                {"event_type": "ACTIVATED", "event_timestamp": "2025-11-23T19:00:00Z", "price": 98000},
                {"event_type": "TP1_HIT", "event_timestamp": "2025-11-23T20:00:00Z", "price": 99000, "pnl_pct": 1.02},
                {"event_type": "TP2_HIT", "event_timestamp": "2025-11-23T21:00:00Z", "price": 100000, "pnl_pct": 2.04},
            ],
        },
    )
    assert "النتيجة المالية للمحاكاة" in view.text
    assert "صفقة مغلقة بتحقيق الأهداف" in view.text
    assert "تفعيل الدخول" in view.text
    assert "الهدف الأول TP1" in view.text
    assert "الهدف الثاني TP2" in view.text
    assert "عدد الأحداث" not in view.text


def test_single_result_renders_pending_order_as_zero_risk():
    view = build_single_result_card(
        _candidate(),
        temporal_route="HISTORICAL_CANDIDATE",
        replay_result={
            "replay_status": "COMPLETED",
            "lifecycle_state": "PENDING_ORDER",
            "net_pnl_pct": 0.0,
            "events_payload": [],
        },
    )
    assert "أمر معلق" in view.text
    assert "0.00%" in view.text
    assert "لم تُفتح صفقة" in view.text
    assert "STILL_ACTIVE" not in view.text


def test_single_result_keeps_partial_coverage_non_final():
    view = build_single_result_card(
        _candidate(),
        temporal_route="HISTORICAL_CANDIDATE",
        replay_result={
            "replay_status": "PARTIAL_WINDOW",
            "coverage_status": "PARTIAL_WINDOW",
            "lifecycle_state": "ACTIVE_POSITION",
        },
    )
    assert "التغطية: <code>PARTIAL_WINDOW</code>" in view.text
    assert "لا توجد نتيجة تداول كاملة" in view.text
