from capitalguard.interfaces.telegram.financial_timeline import (
    format_events_timeline,
    format_financial_replay_result,
)


def test_timeline_renders_financial_events_not_event_count():
    lines = format_events_timeline([
        {"event_type": "TP2_HIT", "event_timestamp": "2025-11-30T16:17:00Z", "price": 89200, "pnl_pct": 1.7},
        {"event_type": "ACTIVATED", "event_timestamp": "2025-11-29T12:43:00Z", "price": 87700},
    ])
    assert lines[0].startswith("▫️ [2025-11-29 12:43 UTC]")
    assert "تفعيل الدخول" in lines[0]
    assert "الهدف الثاني TP2" in lines[1]
    assert "+1.70%" in lines[1]
    assert "عدد الأحداث" not in " ".join(lines)


def test_pending_order_is_not_presented_as_active_trade():
    lines = format_financial_replay_result({
        "replay_status": "COMPLETED",
        "lifecycle_state": "PENDING_ORDER",
        "net_pnl_pct": 0.0,
        "events_payload": [],
    })
    text = "\n".join(lines)
    assert "أمر معلق" in text
    assert "0.00%" in text
    assert "لم تُفتح صفقة" in text
    assert "ACTIVE_POSITION" not in text


def test_active_position_has_explicit_financial_lifecycle():
    lines = format_financial_replay_result({
        "replay_status": "COMPLETED",
        "lifecycle_state": "ACTIVE_POSITION",
        "net_pnl_pct": 0.35,
        "events_payload": [
            {"event_type": "ACTIVATED", "event_timestamp": "2026-08-26T15:50:00Z", "price": 78049.5},
        ],
    })
    text = "\n".join(lines)
    assert "صفقة نشطة" in text
    assert "+0.35%" in text
    assert "تفعيل الدخول" in text


def test_closed_unverifiable_preserves_uncertainty_without_losing_financial_state():
    lines = format_financial_replay_result({
        "replay_status": "COMPLETED_UNVERIFIABLE",
        "lifecycle_state": "CLOSED_UNVERIFIABLE",
        "net_pnl_pct": -0.46,
        "events_payload": [
            {"event_type": "STOP_LOSS", "event_timestamp": "2025-11-17T03:00:00Z", "price": 93000, "pnl_pct": -0.46},
        ],
    })
    text = "\n".join(lines)
    assert "إغلاق مالي محسوم" in text
    assert "-0.46%" in text
    assert "وقف الخسارة" in text
    assert "COMPLETED_UNVERIFIABLE" not in text.split("الحالة المالية:", 1)[-1].split("\n", 1)[0]
