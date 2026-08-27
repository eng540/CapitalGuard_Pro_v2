"""Pure Telegram presentation models for forwarded-signal intake.

This module deliberately contains no persistence, parsing, routing, market, or
lifecycle logic. It translates already-authorized Core read data into safe
Telegram text and keyboard models.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from html import escape
from typing import Any, Callable, Mapping, Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


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
    """Presentation-only result consumed by a Telegram handler."""

    text: str
    reply_markup: InlineKeyboardMarkup | None
    visual_state: VisualCardState
    actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class BatchSummaryView:
    """Presentation-only summary for a transport/import batch."""

    text: str
    reply_markup: InlineKeyboardMarkup | None
    actions: tuple[str, ...] = ()


_ACTION_LABELS: dict[str, str] = {
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

_INTERNAL_FIELDS = {
    "batch_id",
    "claim_status",
    "receipt_id",
    "replay_gate",
    "replay_gate_reasons",
    "temporal_route",
    "temporal_mode",
    "reason_codes",
    "source_uri",
    "correlation_id",
}


def _value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _text(value: Any, default: str = "—") -> str:
    if value is None or value == "":
        return default
    return escape(str(value))


def _normalize_actions(actions: Sequence[str] | None) -> tuple[str, ...]:
    if not actions:
        return ()
    normalized: list[str] = []
    for action in actions:
        value = str(action).strip().upper()
        if value in _ACTION_LABELS and value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def _state_for(
    *,
    visual_state: VisualCardState | str | None,
    internal_status: str | None,
    substatus: str | None,
) -> VisualCardState:
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
    normalized = str(route or "").strip().upper()
    return {
        "LIVE_REVIEW": "توصية حية",
        "HISTORICAL_CANDIDATE": "توصية تاريخية",
        "TIMELINE_EVENT": "تحديث زمني",
        "CLOSED_EVENT": "حدث إغلاق",
        "QUARANTINE": "تم الاستخراج؛ التتبع ينتظر تحقق المصدر",
        "REVISION_REVIEW": "تم الاستخراج؛ يحتاج استكمالًا بسيطًا",
        "DUPLICATE": "مكررة",
    }.get(normalized, "الحالة محدثة")


def _format_targets(targets: Any) -> str:
    if not targets:
        return "—"
    if isinstance(targets, Mapping):
        targets = [targets]
    if isinstance(targets, (str, bytes)):
        return _text(targets)
    if not isinstance(targets, Sequence):
        return _text(targets)
    rendered: list[str] = []
    for index, target in enumerate(targets, start=1):
        if isinstance(target, Mapping):
            price = target.get("price", target.get("value", target.get("target")))
            percentage = target.get("percentage", target.get("allocation"))
            suffix = f" ({_text(percentage)}%)" if percentage is not None else ""
            rendered.append(f"TP{index}: {_text(price)}{suffix}")
        else:
            rendered.append(f"TP{index}: {_text(target)}")
    return "\n".join(rendered) or "—"


def _has_complete_extraction(candidate: Any) -> bool:
    """Keep extraction visibility independent from deferred semantic/replay work."""
    asset = _value(candidate, "asset", _value(candidate, "symbol"))
    side = _value(candidate, "side", _value(candidate, "direction"))
    entry = _value(candidate, "entry", _value(candidate, "entry_price"))
    stop_loss = _value(candidate, "stop_loss", _value(candidate, "sl"))
    targets = _value(candidate, "targets", _value(candidate, "take_profits"))
    return all(value not in (None, "", []) for value in (asset, side, entry, stop_loss, targets))


def _button_markup(
    actions: Sequence[str],
    callback_data_factory: Callable[[str], str] | None,
) -> InlineKeyboardMarkup | None:
    if not actions:
        return None
    factory = callback_data_factory or (lambda action: action)
    buttons = [
        InlineKeyboardButton(_ACTION_LABELS[action], callback_data=factory(action))
        for action in actions
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for index in range(0, len(buttons), 2):
        rows.append(buttons[index : index + 2])
    return InlineKeyboardMarkup(rows)


def build_card(
    candidate: Any,
    *,
    temporal_route: str | None = None,
    source_timestamp: Any = None,
    source_title: Any = None,
    allowed_actions: Sequence[str] | None = None,
    visual_state: VisualCardState | str | None = None,
    internal_status: str | None = None,
    substatus: str | None = None,
    provenance: Mapping[str, Any] | None = None,
    callback_data_factory: Callable[[str], str] | None = None,
) -> TelegramCardView:
    """Build one safe card from already-authorized read data.

    The function never evaluates a temporal route and never invents an action.
    The caller must provide the actions authorized by Core.
    """

    state = _state_for(
        visual_state=visual_state,
        internal_status=internal_status,
        substatus=substatus,
    )
    actions = list(_normalize_actions(allowed_actions))
    normalized_route = str(temporal_route or "").strip().upper()
    if normalized_route in {"HISTORICAL_CANDIDATE", "QUARANTINE", "UNVERIFIED_TIME"}:
        actions = [
            action
            for action in actions
            if action not in {CardAction.ACCEPT_LIVE_REVIEW.value, CardAction.RECOVER_REVIEW.value}
        ]
    provenance = provenance or {}
    conflict = bool(provenance.get("conflict")) or str(substatus or "").upper() == "CONFLICT"
    if conflict:
        state = VisualCardState.INCOMPLETE
    elif state == VisualCardState.INCOMPLETE and _has_complete_extraction(candidate):
        # A deferred semantic/replay decision must not hide fields already extracted.
        state = VisualCardState.COMPLETE

    status_title = {
        VisualCardState.COMPLETE: "تم استخراج التوصية",
        VisualCardState.INCOMPLETE: "تم الاستخراج ويحتاج استكمالًا بسيطًا",
        VisualCardState.UNAVAILABLE: "تعذر تجهيز التوصية مؤقتًا",
    }[state]
    route_label = _route_badge(temporal_route)
    asset = _value(candidate, "asset", _value(candidate, "symbol"))
    side = _value(candidate, "side", _value(candidate, "direction"))
    entry = _value(candidate, "entry", _value(candidate, "entry_price"))
    stop_loss = _value(candidate, "stop_loss", _value(candidate, "sl"))
    targets = _value(candidate, "targets", _value(candidate, "take_profits"))

    lines = [
        f"<b>{status_title}</b>",
        f"الحالة: {_text(route_label)}",
        f"المصدر: {_text(source_title)}",
        f"وقت النشر: {_text(source_timestamp)}",
        "",
        f"الأصل: <code>{_text(asset)}</code>",
        f"الاتجاه: <code>{_text(side)}</code>",
        f"الدخول: <code>{_text(entry)}</code>",
        f"الأهداف:\n<code>{_format_targets(targets)}</code>",
        f"وقف الخسارة: <code>{_text(stop_loss)}</code>",
    ]
    if conflict:
        lines.extend(["", "توجد قيم متعارضة. راجعها وعدّل القيمة الصحيحة إذا لزم."])
    elif state == VisualCardState.INCOMPLETE:
        lines.extend(["", "يمكنك إكمال أو تعديل القيم من زر التعديل في Web."])
    elif state == VisualCardState.UNAVAILABLE:
        lines.extend(["", "يمكنك إعادة المحاولة أو استخدام الإدخال اليدوي إذا كان متاحًا."])

    return TelegramCardView(
        text="\n".join(lines),
        reply_markup=_button_markup(actions, callback_data_factory),
        visual_state=state,
        actions=tuple(actions),
    )


def build_single_result_card(
    candidate: Any,
    *,
    temporal_route: str | None = None,
    source_timestamp: Any = None,
    source_title: Any = None,
    allowed_actions: Sequence[str] | None = None,
    visual_state: VisualCardState | str | None = None,
    internal_status: str | None = None,
    substatus: str | None = None,
    provenance: Mapping[str, Any] | None = None,
    financial_outcome: Mapping[str, Any] | None = None,
    replay_result: Mapping[str, Any] | None = None,
    callback_data_factory: Callable[[str], str] | None = None,
) -> TelegramCardView:
    """Build a single-forward card with extraction and explicitly sourced outcome data."""

    base = build_card(
        candidate,
        temporal_route=temporal_route,
        source_timestamp=source_timestamp,
        source_title=source_title,
        allowed_actions=allowed_actions,
        visual_state=visual_state,
        internal_status=internal_status,
        substatus=substatus,
        provenance=provenance,
        callback_data_factory=callback_data_factory,
    )
    lines = [base.text, "", "<b>نتيجة ما عمله النظام</b>"]
    replay = replay_result or {}
    outcome = dict(financial_outcome or {})
    if outcome.get("exit_price") is None:
        outcome["exit_price"] = _value(candidate, "exit_price")
    replay_status = str(replay.get("replay_status") or "").upper()
    if replay_status in {"COMPLETED", "COMPLETED_UNVERIFIABLE"}:
        if replay_status == "COMPLETED_UNVERIFIABLE":
            lines.append("المحاكاة التاريخية: اكتملت، لكن بيانات السوق غير قابلة للتحقق؛ لا تُستخدم كنتيجة نهائية أو للترتيب.")
        else:
            lines.append("المحاكاة التاريخية: اكتملت وفق بيانات السوق المتاحة.")
        for label, key in (("الحالة", "replay_status"), ("عدد الأحداث", "event_count"), ("آخر حدث", "last_event"), ("دورة الحياة", "lifecycle_status")):
            value = replay.get(key)
            if value is not None:
                lines.append(f"{label}: <code>{_text(value)}</code>")
    elif replay:
        replay_message = {
            "BLOCKED": "المحاكاة التاريخية مؤجلة؛ نتيجة الاستخراج جاهزة.",
            "REVIEW_REQUIRED": "المحاكاة التاريخية تنتظر استكمال القيم.",
            "FAILED": "تعذر تشغيل المحاكاة التاريخية الآن؛ تم حفظ الاستخراج.",
            "PARTIAL": "المحاكاة التاريخية جزئية؛ تم حفظ ما توفر.",
        }.get(replay_status, "المحاكاة التاريخية لم تكتمل بعد؛ تم حفظ الاستخراج.")
        lines.append(replay_message)
    elif outcome.get("status") or outcome.get("reported_pnl_pct") is not None or outcome.get("derived_pnl_pct") is not None:
        lines.append("النتيجة الموجودة في الرسالة المصدر (لم تُعتبر Replay موثقًا):")
        if outcome.get("status") is not None:
            lines.append(f"الحالة: <code>{_text(outcome.get('status'))}</code>")
        if outcome.get("reported_pnl_pct") is not None:
            lines.append(f"النتيجة المذكورة: <code>{_text(outcome.get('reported_pnl_pct'))}%</code>")
        if outcome.get("derived_pnl_pct") is not None:
            lines.append(f"النتيجة المحسوبة من الدخول والخروج: <code>{_text(outcome.get('derived_pnl_pct'))}%</code>")
        if outcome.get("exit_price") is not None:
            lines.append(f"سعر الخروج: <code>{_text(outcome.get('exit_price'))}</code>")
        lines.append("المحاكاة السوقية التاريخية تحتاج Evidence وReplay مستقلين.")
    else:
        lines.append("المحاكاة التاريخية: لم تُنفذ بعد")
        lines.append("تم حفظ الاستخراج، وتحتاج النتيجة إلى بيانات السوق وReplay قبل اعتبارها محققة.")
    return TelegramCardView(
        text="\n".join(lines),
        reply_markup=base.reply_markup,
        visual_state=base.visual_state,
        actions=base.actions,
    )


def build_batch_summary(
    summary: Any,
    *,
    allowed_actions: Sequence[str] | None = None,
    callback_data_factory: Callable[[str], str] | None = None,
    extracted_items: Sequence[Any] | None = None,
) -> BatchSummaryView:
    """Build a concise user-facing summary without operational identifiers."""

    actions = _normalize_actions(allowed_actions)
    total = _value(summary, "total_records", _value(summary, "total", 0))
    complete = _value(summary, "complete_records", _value(summary, "accepted_records", 0))
    incomplete = _value(summary, "incomplete_records", _value(summary, "partial_count", 0))
    unavailable = _value(summary, "unavailable_records", _value(summary, "failed_records", 0))
    duplicate = _value(summary, "duplicate_records", 0)
    processed = _value(summary, "processed_records", None)
    source_title = _value(summary, "source_title", None)
    period = _value(summary, "period", None)

    lines = ["<b>ملخص معالجة الدفعة</b>"]
    if source_title:
        lines.append(f"المصدر: {_text(source_title)}")
    if period:
        lines.append(f"الفترة: {_text(period)}")
    if processed is not None and total:
        lines.append(f"تمت المعالجة: {_text(processed)} من {_text(total)}")
    else:
        lines.append(f"تم الاستلام: {_text(total)}")
    lines.extend(
        [
            f"مكتملة: {_text(complete)}",
            f"تحتاج استكمالًا: {_text(incomplete)}",
            f"تعذر تجهيزها: {_text(unavailable)}",
        ]
    )
    if duplicate:
        lines.append(f"مكررة: {_text(duplicate)}")
    if extracted_items:
        lines.extend(["", "<b>عينات مما استُخرج:</b>"])
        for index, item in enumerate(extracted_items[:3], start=1):
            asset = _value(item, "asset", _value(item, "symbol"))
            side = _value(item, "side", _value(item, "direction"))
            entry = _value(item, "entry", _value(item, "entry_price"))
            stop_loss = _value(item, "stop_loss", _value(item, "sl"))
            targets = _value(item, "targets", _value(item, "take_profits"))
            lines.append(f"{index}. <code>{_text(asset)}</code> · {_text(side)} · دخول {_text(entry)} · وقف {_text(stop_loss)}")
            if targets:
                lines.append(f"   الأهداف: {_text(_format_targets(targets).replace(chr(10), '، '))}")
            replay = _value(item, "_replay", {}) or {}
            replay_status = replay.get("replay_status")
            if replay_status:
                detail = f"المحاكاة: {_text(replay_status)} · أحداث {_text(replay.get('event_count', 0))}"
                if replay.get("last_event"):
                    detail += f" · آخر حدث {_text(replay.get('last_event'))}"
                lines.append(f"   {detail}")
    if incomplete or unavailable:
        lines.append("تظهر التفاصيل الكاملة لكل عنصر، ويمكنك تعديل القيم الناقصة بسرعة من Web.")
    else:
        lines.append("اكتملت المعالجة دون استثناءات ظاهرة.")

    return BatchSummaryView(
        text="\n".join(lines),
        reply_markup=_button_markup(actions, callback_data_factory),
        actions=actions,
    )


__all__ = [
    "BatchSummaryView",
    "CardAction",
    "TelegramCardView",
    "VisualCardState",
    "build_batch_summary",
    "build_card",
    "build_single_result_card",
]
