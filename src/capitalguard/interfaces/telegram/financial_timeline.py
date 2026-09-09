"""Pure financial timeline formatting for historical replay results.

This module is presentation-only: it consumes already-authorized replay data and
never infers trading events that are not present in the supplied evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any, Mapping, Sequence


_LIFECYCLE_LABELS = {
    "PENDING_ORDER": "أمر معلق — لم يتفعل الدخول",
    "ACTIVE_POSITION": "صفقة نشطة — الدخول تحقق ولم تُغلق بعد",
    "CLOSED_STOP": "صفقة مغلقة عند وقف الخسارة",
    "CLOSED_TARGETS": "صفقة مغلقة بتحقيق الأهداف",
    "CLOSED_UNVERIFIABLE": "إغلاق مالي محسوم لكن ترتيب الأحداث غير قابل للتحقق بالكامل",
}

_EVENT_LABELS = {
    "ACTIVATED": "🚪 تفعيل الدخول",
    "ENTRY": "🚪 تفعيل الدخول",
    "ENTRY_HIT": "🚪 تفعيل الدخول",
    "TP1_HIT": "🎯 الهدف الأول TP1",
    "TP2_HIT": "🎯 الهدف الثاني TP2",
    "TP3_HIT": "🎯 الهدف الثالث TP3",
    "TARGET_HIT": "🎯 تحقيق هدف",
    "STOP_LOSS": "🛑 وقف الخسارة",
    "SL_HIT": "🛑 وقف الخسارة",
    "CLOSED_STOP": "🛑 إغلاق عند الوقف",
    "CLOSED_TARGETS": "🏁 إغلاق بتحقيق الأهداف",
    "FINAL_CLOSE": "🏁 الإغلاق النهائي",
}


def _value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _text(value: Any, default: str = "—") -> str:
    if value is None or value == "":
        return default
    return escape(str(value))


def _time(value: Any) -> str:
    if value in (None, ""):
        return "—"
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return escape(parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    except (TypeError, ValueError, OverflowError):
        return _text(value)


def _event_pnl(event: Any) -> Any:
    for key in ("pnl_pct", "net_pnl_pct", "realized_pnl_pct", "pnl"):
        value = _value(event, key)
        if value is not None:
            return value
    data = _value(event, "event_data", {}) or {}
    if isinstance(data, Mapping):
        for key in ("pnl_pct", "net_pnl_pct", "realized_pnl_pct", "pnl"):
            if data.get(key) is not None:
                return data[key]
    return None


def _event_name(event: Any) -> str:
    raw = str(_value(event, "event_type", _value(event, "type", "EVENT")) or "EVENT").upper()
    return _EVENT_LABELS.get(raw, f"📌 {raw}")


def _event_line(event: Any) -> str:
    timestamp = _time(_value(event, "event_timestamp", _value(event, "timestamp")))
    label = _event_name(event)
    price = _value(event, "price")
    pnl = _event_pnl(event)
    line = f"▫️ [{timestamp}] {_text(label)}"
    if price is not None:
        line += f" عند {_text(price)}"
    if pnl is not None:
        try:
            line += f" ({float(pnl):+.2f}%)"
        except (TypeError, ValueError):
            line += f" ({_text(pnl)}%)"
    return line


def _normalize_events(events: Any) -> list[Any]:
    if events is None:
        return []
    if isinstance(events, Mapping):
        events = [events]
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
        return []
    return list(events)


def format_events_timeline(events: Any, *, empty_message: str | None = None) -> list[str]:
    """Return a chronological, human-readable financial event timeline."""
    normalized = _normalize_events(events)
    if not normalized:
        return [empty_message] if empty_message else []
    normalized.sort(key=lambda event: str(_value(event, "event_timestamp", _value(event, "timestamp", ""))))
    return [_event_line(event) for event in normalized]


def format_financial_replay_result(
    replay: Mapping[str, Any] | None,
    *,
    events: Any = None,
) -> list[str]:
    """Format replay truth without exposing internal IDs or implementation reasons."""
    replay = replay or {}
    lifecycle = str(
        replay.get("lifecycle_status")
        or replay.get("lifecycle_state")
        or ""
    ).upper()
    status = str(replay.get("replay_status") or replay.get("status") or "").upper()
    if not lifecycle:
        lifecycle = "UNKNOWN"

    lines = ["<b>📊 النتيجة المالية للمحاكاة</b>"]
    lines.append(f"الحالة المالية: <code>{_text(_LIFECYCLE_LABELS.get(lifecycle, lifecycle))}</code>")
    if status:
        if status == "COMPLETED_UNVERIFIABLE":
            lines.append("المحاكاة: اكتملت، مع تعذر التحقق الكامل من ترتيب بعض الأحداث اللحظية.")
        elif status == "COMPLETED":
            lines.append("المحاكاة: اكتملت وفق الأدلة المتاحة.")
        elif status in {"PARTIAL_WINDOW", "REPLAY_PARTIAL"}:
            lines.append("المحاكاة: جزئية؛ لا تُعد النتيجة المالية مكتملة.")
        else:
            lines.append(f"حالة المحاكاة: <code>{_text(status)}</code>")

    net_pnl = None
    for key in ("net_pnl_pct", "pnl_pct", "realized_pnl_pct"):
        if replay.get(key) is not None:
            net_pnl = replay[key]
            break
    if net_pnl is not None:
        try:
            value = float(net_pnl)
            marker = "💚 ربح" if value > 0 else "💔 خسارة" if value < 0 else "➖ تعادل"
            lines.append(f"النتيجة الصافية: <code>{value:+.2f}%</code> ({marker})")
        except (TypeError, ValueError):
            lines.append(f"النتيجة الصافية: <code>{_text(net_pnl)}</code>")
    elif lifecycle == "PENDING_ORDER":
        lines.append("النتيجة الصافية: <code>0.00%</code> — لم يدخل رأس المال حيز المخاطرة.")

    timeline = format_events_timeline(events if events is not None else replay.get("events"))
    if lifecycle == "PENDING_ORDER" and not timeline:
        lines.extend([
            "",
            "<b>📜 سجل مسار التوصية</b>",
            "▫️ لم يلامس السعر نقطة الدخول خلال الأفق التاريخي المتحقق؛ لم تُفتح صفقة.",
        ])
    elif timeline:
        lines.extend(["", "<b>📜 سجل مسار الصفقة والأحداث</b>", *timeline])
    if lifecycle in _LIFECYCLE_LABELS:
        lines.append(f"دورة الحياة: <code>{lifecycle}</code>")
    if replay.get("coverage_status"):
        lines.append(f"التغطية: <code>{_text(replay.get('coverage_status'))}</code>")
    return lines


__all__ = ["format_events_timeline", "format_financial_replay_result"]
