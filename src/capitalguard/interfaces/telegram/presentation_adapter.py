"""Pure Telegram presentation models for forwarded-signal intake."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from html import escape
from typing import Any, Callable, Mapping, Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .financial_timeline import format_financial_replay_result


class VisualCardState(str, Enum):
    COMPLETE = "CARD_COMPLETE"
    INCOMPLETE = "CARD_INCOMPLETE"
    UNAVAILABLE = "CARD_UNAVAILABLE"


class CardAction(str, Enum):
    ACCEPT_LIVE_REVIEW = "ACCEPT_LIVE_REVIEW"
    IMPORT_HISTORICAL = "IMPORT_HISTORICAL"
    TRACK_ONLY = "TRACK_ONLY"
    RECOVER_REVIEW = "RECOVER_REVIEW"
    EDIT = "EDIT"
    COMPLETE_DATA = "COMPLETE_DATA"
    ACCEPT_TEXT = "ACCEPT_TEXT"
    ACCEPT_IMAGE = "ACCEPT_IMAGE"
    MANUAL_ENTRY = "MANUAL_ENTRY"
    RETRY = "RETRY"
    PROVIDE_SOURCE = "PROVIDE_SOURCE"
    DISMISS = "DISMISS"


@dataclass(frozen=True)
class TelegramCardView:
    text: str
    reply_markup: InlineKeyboardMarkup | None
    visual_state: VisualCardState
    actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class BatchSummaryView:
    text: str
    reply_markup: InlineKeyboardMarkup | None
    actions: tuple[str, ...] = ()


_ACTION_LABELS = {
    CardAction.ACCEPT_LIVE_REVIEW.value: "تأكيد المراجعة الحية",
    CardAction.IMPORT_HISTORICAL.value: "المحاكاة التاريخية",
    CardAction.TRACK_ONLY.value: "تتبع فقط",
    CardAction.RECOVER_REVIEW.value: "استعادة للمراجعة",
    CardAction.EDIT.value: "تعديل القيم",
    CardAction.COMPLETE_DATA.value: "إكمال البيانات",
    CardAction.ACCEPT_TEXT.value: "اعتماد النص",
    CardAction.ACCEPT_IMAGE.value: "اعتماد الصورة",
    CardAction.MANUAL_ENTRY.value: "إدخال يدوي",
    CardAction.RETRY.value: "إعادة المحاولة",
    CardAction.PROVIDE_SOURCE.value: "إثبات المصدر",
    CardAction.DISMISS.value: "إلغاء",
}


def _value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _text(value: Any, default: str = "—") -> str:
    if value is None or value == "":
        return default
    return escape(str(value))


def _source_time_text(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        from datetime import datetime, timezone
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return escape(parsed.strftime("%Y-%m-%d %H:%M:%S UTC"))
        return escape(parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    except (TypeError, ValueError, OverflowError):
        return _text(value)


def _normalize_actions(actions: Sequence[str] | None) -> tuple[str, ...]:
    normalized = []
    for action in actions or ():
        value = str(action).strip().upper()
        if value in _ACTION_LABELS and value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def _state_for(*, visual_state, internal_status, substatus) -> VisualCardState:
    if visual_state is not None:
        if isinstance(visual_state, VisualCardState):
            return visual_state
        try:
            return VisualCardState(str(visual_state).upper())
        except ValueError:
            pass
    status = " ".join(str(item or "") for item in (internal_status, substatus)).upper()
    if any(token in status for token in ("INCOMPLETE", "PARTIAL", "CONFLICT", "REVIEW_REQUIRED")):
        return VisualCardState.INCOMPLETE
    if any(token in status for token in ("UNAVAILABLE", "RETRY", "QUARANTINE", "UNVERIFIED", "FAILED")):
        return VisualCardState.UNAVAILABLE
    return VisualCardState.COMPLETE


def _route_badge(route: Any) -> str:
    return {
        "LIVE_REVIEW": "توصية حية",
        "HISTORICAL_CANDIDATE": "توصية تاريخية",
        "TIMELINE_EVENT": "تحديث زمني",
        "CLOSED_EVENT": "حدث إغلاق",
        "QUARANTINE": "تم الاستخراج؛ التتبع ينتظر تحقق المصدر",
        "REVISION_REVIEW": "تم الاستخراج؛ يحتاج استكمالًا بسيطًا",
        "DUPLICATE": "♻️ التوصية مسجلة مسبقاً",
        "ALREADY_REGISTERED": "♻️ التوصية مسجلة مسبقاً",
    }.get(str(route or "").strip().upper(), "الحالة محدثة")


def _format_targets(targets: Any) -> str:
    if not targets:
        return "—"
    if isinstance(targets, Mapping):
        targets = [targets]
    if isinstance(targets, (str, bytes)) or not isinstance(targets, Sequence):
        return _text(targets)
    rendered = []
    for index, target in enumerate(targets, 1):
        if isinstance(target, Mapping):
            price = target.get("price", target.get("value", target.get("target")))
            percentage = target.get("percentage", target.get("allocation"))
            suffix = f" ({_text(percentage)}%)" if percentage is not None else ""
            rendered.append(f"TP{index}: {_text(price)}{suffix}")
        else:
            rendered.append(f"TP{index}: {_text(target)}")
    return "\n".join(rendered) or "—"


def _has_complete_extraction(candidate: Any) -> bool:
    values = (
        _value(candidate, "asset", _value(candidate, "symbol")),
        _value(candidate, "side", _value(candidate, "direction")),
        _value(candidate, "entry", _value(candidate, "entry_price")),
        _value(candidate, "stop_loss", _value(candidate, "sl")),
        _value(candidate, "targets", _value(candidate, "take_profits")),
    )
    return all(value not in (None, "", []) for value in values)


def _button_markup(actions, callback_data_factory):
    if not actions:
        return None
    factory = callback_data_factory or (lambda action: action)
    buttons = [InlineKeyboardButton(_ACTION_LABELS[action], callback_data=factory(action)) for action in actions]
    return InlineKeyboardMarkup([buttons[index:index + 2] for index in range(0, len(buttons), 2)])


def build_card(candidate: Any, *, temporal_route=None, source_timestamp=None, source_title=None,
               allowed_actions=None, visual_state=None, internal_status=None, substatus=None,
               provenance=None, callback_data_factory=None) -> TelegramCardView:
    state = _state_for(visual_state=visual_state, internal_status=internal_status, substatus=substatus)
    actions = list(_normalize_actions(allowed_actions))
    route = str(temporal_route or "").strip().upper()
    if route in {"HISTORICAL_CANDIDATE", "QUARANTINE", "UNVERIFIED_TIME"}:
        actions = [a for a in actions if a not in {CardAction.ACCEPT_LIVE_REVIEW.value, CardAction.RECOVER_REVIEW.value}]
    provenance = provenance or {}
    conflict = bool(provenance.get("conflict")) or str(substatus or "").upper() == "CONFLICT"
    if conflict:
        state = VisualCardState.INCOMPLETE
    elif state == VisualCardState.INCOMPLETE and _has_complete_extraction(candidate):
        state = VisualCardState.COMPLETE
    status_title = {
        VisualCardState.COMPLETE: "تم استخراج التوصية",
        VisualCardState.INCOMPLETE: "تم الاستخراج ويحتاج استكمالًا بسيطًا",
        VisualCardState.UNAVAILABLE: "تعذر تجهيز التوصية مؤقتًا",
    }[state]
    asset = _value(candidate, "asset", _value(candidate, "symbol"))
    side = _value(candidate, "side", _value(candidate, "direction"))
    entry = _value(candidate, "entry", _value(candidate, "entry_price"))
    stop_loss = _value(candidate, "stop_loss", _value(candidate, "sl"))
    targets = _value(candidate, "targets", _value(candidate, "take_profits"))
    market = _value(candidate, "market")
    lines = [
        f"<b>{status_title}</b>", f"الحالة: {_text(_route_badge(temporal_route))}",
        f"المصدر: {_text(source_title)}", f"وقت النشر: {_source_time_text(source_timestamp)}", "",
        f"الأصل: <code>{_text(asset)}</code>", f"الاتجاه: <code>{_text(side)}</code>",
        f"السوق: <code>{_text(market)}</code>", f"الدخول: <code>{_text(entry)}</code>",
        f"الأهداف:\n<code>{_format_targets(targets)}</code>", f"وقف الخسارة: <code>{_text(stop_loss)}</code>",
    ]
    if conflict:
        lines.extend(["", "توجد قيم متعارضة. راجعها وعدّل القيمة الصحيحة إذا لزم."])
    elif state == VisualCardState.INCOMPLETE:
        lines.extend(["", "يمكنك إكمال أو تعديل القيم من زر التعديل في Web."])
    elif state == VisualCardState.UNAVAILABLE:
        lines.extend(["", "يمكنك إعادة المحاولة أو استخدام الإدخال اليدوي إذا كان متاحًا."])
    return TelegramCardView("\n".join(lines), _button_markup(actions, callback_data_factory), state, tuple(actions))


def build_single_result_card(candidate: Any, *, temporal_route=None, source_timestamp=None, source_title=None,
                             allowed_actions=None, visual_state=None, internal_status=None, substatus=None,
                             provenance=None, financial_outcome=None, replay_result=None,
                             callback_data_factory=None) -> TelegramCardView:
    base = build_card(candidate, temporal_route=temporal_route, source_timestamp=source_timestamp,
                      source_title=source_title, allowed_actions=allowed_actions, visual_state=visual_state,
                      internal_status=internal_status, substatus=substatus, provenance=provenance,
                      callback_data_factory=callback_data_factory)
    lines = [base.text, "", "<b>نتيجة ما عمله النظام</b>"]
    replay = dict(replay_result or {})
    outcome = dict(financial_outcome or {})
    if outcome.get("exit_price") is None:
        outcome["exit_price"] = _value(candidate, "exit_price")
    status = str(replay.get("replay_status") or replay.get("status") or "").upper()
    if status in {"COMPLETED", "COMPLETED_UNVERIFIABLE", "ACTIVE", "PENDING_ORDER", "ACTIVE_POSITION"}:
        if status in {"ACTIVE", "ACTIVE_POSITION", "PENDING_ORDER"} and not replay.get("lifecycle_status"):
            replay["lifecycle_status"] = status if status != "ACTIVE" else "ACTIVE_POSITION"
        lines.extend(format_financial_replay_result(replay, events=replay.get("events")))
    elif replay:
        messages = {
            "BLOCKED": "المحاكاة التاريخية مؤجلة؛ نتيجة الاستخراج جاهزة.",
            "REVIEW_REQUIRED": "المحاكاة التاريخية تنتظر استكمال القيم؛ يمكنك تعديل الاستخراج.",
            "REPLAY_PENDING": "المحاكاة التاريخية قيد الانتظار؛ تم حفظ الاستخراج.",
            "FAILED": "تعذر تشغيل المحاكاة التاريخية الآن؛ تم حفظ الاستخراج.",
            "REPLAY_FAILED": "تعذر إكمال المحاكاة التاريخية؛ تم حفظ الاستخراج.",
            "PROGRESSION_FAILED": "تعذر تجهيز المحاكاة التاريخية؛ تم حفظ الاستخراج.",
            "PROVIDER_UNAVAILABLE": "تعذر جلب بيانات السوق الآن؛ تم حفظ الاستخراج ويمكن إعادة المحاولة لاحقًا.",
            "PARTIAL_WINDOW": "⚠️ وصلت المحاكاة إلى G6، لكن التغطية التاريخية جزئية؛ لن يتم احتساب إغلاق أو ربح كامل دون تغطية زمنية كافية.",
            "PARTIAL": "المحاكاة التاريخية جزئية؛ تم حفظ ما توفر.",
        }
        lines.append(messages.get(status, "المحاكاة التاريخية لم تكتمل بعد؛ تم حفظ الاستخراج."))
        if status == "PARTIAL_WINDOW":
            coverage = replay.get("coverage_status") or "PARTIAL_WINDOW"
            ratio = replay.get("coverage_ratio")
            lines.append(f"التغطية: <code>{_text(coverage)}</code>" + (f" · {float(ratio) * 100:.2f}%" if ratio is not None else ""))
            lines.append("تم حفظ الاستخراج وبيانات G5؛ لا توجد نتيجة تداول كاملة ما لم تصبح التغطية FULL.")
    elif outcome.get("status") or outcome.get("reported_pnl_pct") is not None or outcome.get("derived_pnl_pct") is not None:
        lines.append("النتيجة الموجودة في الرسالة المصدر (لم تُعتبر Replay موثقًا):")
        if outcome.get("status") is not None: lines.append(f"الحالة: <code>{_text(outcome['status'])}</code>")
        if outcome.get("reported_pnl_pct") is not None: lines.append(f"النتيجة المذكورة: <code>{_text(outcome['reported_pnl_pct'])}%</code>")
        if outcome.get("derived_pnl_pct") is not None: lines.append(f"النتيجة المحسوبة من الدخول والخروج: <code>{_text(outcome['derived_pnl_pct'])}%</code>")
        if outcome.get("exit_price") is not None: lines.append(f"سعر الخروج: <code>{_text(outcome['exit_price'])}</code>")
        lines.append("المحاكاة السوقية التاريخية تحتاج Evidence وReplay مستقلين.")
    else:
        lines.extend(["المحاكاة التاريخية: لم تُنفذ بعد", "تم حفظ الاستخراج، وتحتاج النتيجة إلى بيانات السوق وReplay قبل اعتبارها محققة."])
    return TelegramCardView("\n".join(lines), base.reply_markup, base.visual_state, base.actions)


def build_batch_summary(summary: Any, *, allowed_actions=None, callback_data_factory=None, extracted_items=None) -> BatchSummaryView:
    actions = _normalize_actions(allowed_actions)
    total = _value(summary, "total_records", _value(summary, "total", 0))
    complete = _value(summary, "complete_records", _value(summary, "accepted_records", 0))
    incomplete = _value(summary, "incomplete_records", _value(summary, "partial_count", 0))
    unavailable = _value(summary, "unavailable_records", _value(summary, "failed_records", 0))
    duplicate = _value(summary, "duplicate_records", 0)
    processed = _value(summary, "processed_records", None)
    source_title = _value(summary, "source_title", None)
    lines = ["<b>ملخص معالجة الدفعة</b>"]
    if source_title: lines.append(f"المصدر: {_text(source_title)}")
    period = _value(summary, "period", None)
    if period: lines.append(f"الفترة: {_text(period)}")
    lines.append(f"تمت المعالجة: {_text(processed)} من {_text(total)}" if processed is not None and total else f"تم الاستلام: {_text(total)}")
    lines.extend([f"مكتملة: {_text(complete)}", f"تحتاج استكمالًا: {_text(incomplete)}", f"تعذر تجهيزها: {_text(unavailable)}"])
    if duplicate: lines.append(f"مكررة: {_text(duplicate)}")
    replay_status = str(_value(summary, "replay_status", "") or "").upper()
    replay_completed = _value(summary, "replay_completed_records", None)
    replay_failed = _value(summary, "replay_failed_records", None)
    replay_pending = _value(summary, "replay_pending_records", None)
    if replay_status or any(v is not None for v in (replay_completed, replay_failed, replay_pending)):
        lines.extend(["", "<b>نتيجة المحاكاة التاريخية:</b>"])
        if replay_completed is not None: lines.append(f"مكتملة: {_text(replay_completed)}")
        if replay_failed is not None: lines.append(f"تعذر إكمالها: {_text(replay_failed)}")
        if replay_pending is not None: lines.append(f"تنتظر نتيجة: {_text(replay_pending)}")
        if replay_status == "COMPLETED_UNVERIFIABLE": lines.append("⚠️ اكتملت المحاكاة وتم حسم الإغلاق المالي، مع تعذر التحقق الكامل من ترتيب الصفقات اللحظية داخل بعض الشموع.")
        elif replay_status == "COMPLETED": lines.append("اكتملت المحاكاة وفق بيانات السوق المتاحة.")
        elif replay_failed: lines.append("تم حفظ الاستخراج؛ بعض النتائج التاريخية لم تكتمل.")
        elif replay_pending: lines.append("تم حفظ الاستخراج؛ بعض النتائج التاريخية لم تكتمل بعد.")
    if extracted_items:
        lines.extend(["", "<b>عينات مما استُخرج:</b>"])
        for index, item in enumerate(extracted_items[:3], 1):
            asset = _value(item, "asset", _value(item, "symbol")); side = _value(item, "side", _value(item, "direction"))
            entry = _value(item, "entry", _value(item, "entry_price")); stop = _value(item, "stop_loss", _value(item, "sl"))
            targets = _value(item, "targets", _value(item, "take_profits"))
            lines.append(f"{index}. <code>{_text(asset)}</code> · {_text(side)} · دخول {_text(entry)} · وقف {_text(stop)}")
            if targets: lines.append(f"   الأهداف: {_text(_format_targets(targets).replace(chr(10), '، '))}")
            replay = _value(item, "_replay", {}) or {}
            if replay.get("replay_status"):
                detail = f"المحاكاة: {_text(replay['replay_status'])} · أحداث {_text(replay.get('event_count', 0))}"
                if replay.get("last_event"): detail += f" · آخر حدث {_text(replay['last_event'])}"
                lines.append(f"   {detail}")
    if incomplete or unavailable:
        lines.append("تظهر التفاصيل الكاملة لكل عنصر، ويمكنك تعديل القيم الناقصة بسرعة من Web.")
    elif replay_failed or replay_pending:
        lines.append("اكتمل استخراج القيم، وتظهر حالة المحاكاة التاريخية أعلاه.")
    elif replay_status in {"COMPLETED", "COMPLETED_UNVERIFIABLE"}:
        lines.append("اكتملت المعالجة والمحاكاة التاريخية.")
    else:
        lines.append("اكتملت المعالجة دون استثناءات ظاهرة.")
    return BatchSummaryView("\n".join(lines), _button_markup(actions, callback_data_factory), actions)


__all__ = ["BatchSummaryView", "CardAction", "TelegramCardView", "VisualCardState", "build_batch_summary", "build_card", "build_single_result_card"]
