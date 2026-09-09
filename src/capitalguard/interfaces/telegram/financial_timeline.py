"""Pure financial timeline formatting for historical replay results."""
from __future__ import annotations

import json
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
    "ACTIVATED": "🚪 تفعيل الدخول", "ENTRY": "🚪 تفعيل الدخول", "ENTRY_HIT": "🚪 تفعيل الدخول",
    "TP1_HIT": "🎯 الهدف الأول TP1", "TP2_HIT": "🎯 الهدف الثاني TP2", "TP3_HIT": "🎯 الهدف الثالث TP3",
    "TARGET_HIT": "🎯 تحقيق هدف", "STOP_LOSS": "🛑 وقف الخسارة", "SL_HIT": "🛑 وقف الخسارة",
    "CLOSED_STOP": "🛑 إغلاق عند الوقف", "CLOSED_TARGETS": "🏁 إغلاق بتحقيق الأهداف", "FINAL_CLOSE": "🏁 الإغلاق النهائي",
}


def _value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _first(source: Any, keys: Sequence[str], default: Any = None) -> Any:
    for key in keys:
        value = _value(source, key)
        if value is not None and value != "":
            return value
    return default


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
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (TypeError, ValueError):
            data = {}
    if isinstance(data, Mapping):
        for key in ("pnl_pct", "net_pnl_pct", "realized_pnl_pct", "pnl"):
            if data.get(key) is not None:
                return data[key]
    return None


def _event_name(event: Any) -> str:
    raw = str(_first(event, ("event_type", "event", "type"), "EVENT") or "EVENT").upper()
    return _EVENT_LABELS.get(raw, f"📌 {raw}")


def _event_line(event: Any) -> str:
    timestamp = _time(_first(event, ("event_timestamp", "timestamp", "occurred_at", "created_at")))
    label = _event_name(event)
    price = _first(event, ("price", "event_price", "execution_price"))
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
    if isinstance(events, str):
        try:
            events = json.loads(events)
        except (TypeError, ValueError):
            return []
    if isinstance(events, Mapping):
        events = [events]
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
        return []
    return list(events)


def format_events_timeline(events: Any, *, empty_message: str | None = None) -> list[str]:
    normalized = _normalize_events(events)
    if not normalized:
        return [empty_message] if empty_message else []
    normalized.sort(key=lambda event: str(_first(event, ("event_timestamp", "timestamp", "occurred_at", "created_at"), "")))
    return [_event_line(event) for event in normalized]


def _replay_events(replay: Mapping[str, Any]) -> Any:
    for key in ("events", "events_payload", "event_payload"):
        value = replay.get(key)
        if value not in (None, "", []):
            return value
    return None


def format_financial_replay_result(replay: Mapping[str, Any] | None, *, events: Any = None) -> list[str]:
    replay = replay or {}
    lifecycle = str(replay.get("lifecycle_status") or replay.get("lifecycle_state") or "").upper() or "UNKNOWN"
    status = str(replay.get("replay_status") or replay.get("status") or "").upper()
    lines = ["<b>📊 النتيجة المالية للمحاكاة</b>", f"الحالة المالية: <code>{_text(_LIFECYCLE_LABELS.get(lifecycle, lifecycle))}</code>"]
    if status == "COMPLETED_UNVERIFIABLE":
        lines.append("المحاكاة: <code>COMPLETED_UNVERIFIABLE</code> — اكتملت مع تعذر التحقق الكامل من ترتيب بعض الأحداث اللحظية.")
        lines.append("⚠️ الإغلاق المالي حُسم، لكن ترتيب الصفقات اللحظية داخل بعض الشموع لا يمكن إثباته بالكامل.")
    elif status == "COMPLETED":
        lines.append("المحاكاة: اكتملت وفق الأدلة المتاحة.")
    elif status in {"PARTIAL_WINDOW", "REPLAY_PARTIAL"}:
        lines.append("المحاكاة: جزئية؛ لا توجد نتيجة تداول كاملة بسبب نقص التغطية التاريخية.")
    elif status:
        lines.append(f"حالة المحاكاة: <code>{_text(status)}</code>")
    net_pnl = next((replay[k] for k in ("net_pnl_pct", "pnl_pct", "realized_pnl_pct") if replay.get(k) is not None), None)
    if net_pnl is not None:
        try:
            value = float(net_pnl)
            marker = "💚 ربح" if value > 0 else "💔 خسارة" if value < 0 else "➖ تعادل"
            lines.append(f"النتيجة الصافية: <code>{value:+.2f}%</code> ({marker})")
        except (TypeError, ValueError):
            lines.append(f"النتيجة الصافية: <code>{_text(net_pnl)}</code>")
    elif lifecycle == "PENDING_ORDER":
        lines.append("النتيجة الصافية: <code>0.00%</code> — لم يدخل رأس المال حيز المخاطرة.")
    timeline = format_events_timeline(events if events is not None else _replay_events(replay))
    if lifecycle == "PENDING_ORDER" and not timeline:
        lines += ["", "<b>📜 سجل مسار التوصية</b>", "▫️ لم يلامس السعر نقطة الدخول خلال الأفق التاريخي المتحقق؛ لم تُفتح صفقة."]
    elif timeline:
        lines += ["", "<b>📜 سجل مسار الصفقة والأحداث</b>", *timeline]
    if lifecycle in _LIFECYCLE_LABELS:
        lines.append(f"دورة الحياة: <code>{lifecycle}</code>")
    if replay.get("coverage_status"):
        lines.append(f"التغطية: <code>{_text(replay['coverage_status'])}</code>")
    return lines


__all__ = ["format_events_timeline", "format_financial_replay_result"]
