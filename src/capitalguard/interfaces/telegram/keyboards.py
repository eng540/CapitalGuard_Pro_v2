# src/capitalguard/interfaces/telegram/keyboards.py
# --- START OF FINAL, PRODUCTION-READY FILE ---
"""
Keyboards and CallbackBuilder utilities for Telegram UI.

✅ THE FIX: Introduced a robust CallbackBuilder, CallbackNamespace and
CallbackAction enums to produce parseable callback_data and to avoid
ambiguous string formats that caused session/token mismatches.
✅ THE FIX: Implemented safe helpers (_get_attr) and defensive code paths.
"""

import logging
from typing import Iterable, List, Set, Dict, Any, Optional
from enum import Enum, auto

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# ---------------------------
# Callback building utility
# ---------------------------
class CallbackNamespace(str, Enum):
    RECOMMENDATION = "rec"
    PUBLICATION = "pub"
    # add more namespaces as required


class CallbackAction(str, Enum):
    PUBLISH = "publish"
    CHOOSE_CHANNELS = "choose_channels"
    TOGGLE = "toggle"
    BACK = "back"
    ADD_NOTES = "add_notes"
    EDIT_DATA = "edit_data"
    PREVIEW = "preview"
    CANCEL = "cancel"
    # additional actions...


class CallbackBuilder:
    """
    Minimal structured callback data builder/parser.
    Format: <namespace>|<action>|<p1>,<p2>,...
    Example: "pub|toggle|<short_token>,-1001234567890,1"
    """
    SEP_NS = "|"
    SEP_PARAMS = ","

    @staticmethod
    def create(namespace: CallbackNamespace, action: CallbackAction, *params: Any) -> str:
        ns = namespace.value
        act = action.value if isinstance(action, CallbackAction) else str(action)
        params_s = CallbackBuilder.SEP_PARAMS.join(str(p) for p in params) if params else ""
        if params_s:
            return f"{ns}{CallbackBuilder.SEP_NS}{act}{CallbackBuilder.SEP_NS}{params_s}"
        return f"{ns}{CallbackBuilder.SEP_NS}{act}"

    @staticmethod
    def parse(raw: str) -> Dict[str, Any]:
        try:
            if not raw or CallbackBuilder.SEP_NS not in raw:
                return {"namespace": None, "action": None, "params": []}
            parts = raw.split(CallbackBuilder.SEP_NS, 2)
            namespace = parts[0]
            action = parts[1] if len(parts) > 1 else None
            params = []
            if len(parts) == 3 and parts[2]:
                params = parts[2].split(CallbackBuilder.SEP_PARAMS)
            return {"namespace": namespace, "action": action, "params": params}
        except Exception as e:
            logger.exception("CallbackBuilder.parse failed: %s", e)
            return {"namespace": None, "action": None, "params": []}

# ---------------------------
# UI text/buttons helpers
# ---------------------------
class ButtonTexts:
    BACK = "⬅️ عودة"
    CANCEL = "❌ إلغاء"

def _get_attr(obj: Any, attr: str, default=None):
    """Safe attribute/dict getter used for ORM rows or dicts."""
    try:
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(attr, default)
        return getattr(obj, attr, default)
    except Exception:
        return default

# --- Standard keyboards (examples) ---
def asset_choice_keyboard(recent_assets: List[str]) -> InlineKeyboardMarkup:
    if not recent_assets:
        return InlineKeyboardMarkup([[InlineKeyboardButton("✍️ اكتب أصلاً جديدًا", callback_data="asset_new")]])
    buttons = [InlineKeyboardButton(a, callback_data=f"asset_{a}") for a in recent_assets]
    layout = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    layout.append([InlineKeyboardButton("✍️ اكتب أصلاً جديدًا", callback_data="asset_new")])
    return InlineKeyboardMarkup(layout)

def side_market_keyboard(current_market: str = "Futures") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🟢 LONG / {current_market}", callback_data=f"side_LONG"),
            InlineKeyboardButton(f"🔴 SHORT / {current_market}", callback_data=f"side_SHORT"),
        ],
        [InlineKeyboardButton(f"🔄 تغيير السوق (الحالي: {current_market})", callback_data="change_market_menu")],
    ])

def market_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 Futures", callback_data="market_Futures"), InlineKeyboardButton("💎 Spot", callback_data="market_Spot")],
        [InlineKeyboardButton(ButtonTexts.BACK, callback_data="market_back")],
    ])

def order_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Market (دخول فوري)", callback_data="type_MARKET")],
        [InlineKeyboardButton("🎯 Limit (انتظار سعر أفضل)", callback_data="type_LIMIT")],
        [InlineKeyboardButton("🚨 Stop Market (دخول بعد اختراق)", callback_data="type_STOP_MARKET")],
    ])

def review_final_keyboard(review_token: str) -> InlineKeyboardMarkup:
    """
    The final review keyboard shown before publishing.
    NOTE: we pass a `short token` to limit callback length while keeping verification possible.
    """
    short_token = (review_token or "")[:24]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نشر في القنوات الفعّالة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.PUBLISH, short_token))],
        [
            InlineKeyboardButton("📢 اختيار القنوات", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.CHOOSE_CHANNELS, short_token)),
            InlineKeyboardButton("📝 إضافة/تعديل ملاحظات", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.ADD_NOTES, short_token)),
        ],
        [
            InlineKeyboardButton("✏️ تعديل البيانات", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.EDIT_DATA, short_token)),
            InlineKeyboardButton("👁️ معاينة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.PREVIEW, short_token)),
        ],
        [InlineKeyboardButton("❌ إلغاء", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.CANCEL, short_token))],
    ])

# ---------------------------
# Channel picker (fixed)
# ---------------------------
def build_channel_picker_keyboard(
    review_token: str,
    channels: Iterable[dict],
    selected_ids: Set[int],
    page: int = 1,
    per_page: int = 6,
) -> InlineKeyboardMarkup:
    """
    Builds a paginated keyboard of channels. Each button toggles selection.
    Callback format: "pub|toggle|<short_token>,<telegram_chat_id>,<page>"
    ✅ THE FIX: previous versions used ambiguous callback strings and truncated tokens in different ways,
    causing token mismatches and session expiry messages. This version centralizes format and parsing.
    """
    try:
        ch_list = list(channels)
        total = len(ch_list)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))

        start_idx = (page - 1) * per_page
        page_items = ch_list[start_idx:start_idx + per_page]

        rows = []
        for ch in page_items:
            try:
                tg_chat_id = int(_get_attr(ch, "telegram_channel_id", 0) or 0)
                if tg_chat_id == 0:
                    continue
                title = _get_attr(ch, "title") or f"@{_get_attr(ch, 'username')}" or f"قناة {tg_chat_id}"
                short_title = title if len(title) <= 25 else title[:22] + "..."
                is_selected = tg_chat_id in (selected_ids or set())
                status = "✅" if is_selected else "☑️"
                callback = CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.TOGGLE, (review_token or "")[:24], tg_chat_id, page)
                rows.append([InlineKeyboardButton(f"{status} {short_title}", callback_data=callback)])
            except Exception as e:
                logger.warning("Skipping channel row due to error: %s", e)
                continue

        # navigation row
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.BACK, (review_token or "")[:24], 0, page - 1)))
        if total_pages > 1:
            nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            nav.append(InlineKeyboardButton("التالي ➡️", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.BACK, (review_token or "")[:24], 0, page + 1)))

        if nav:
            rows.append(nav)

        # Back to review button
        rows.append([InlineKeyboardButton("⬅️ الرجوع للمراجعة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.BACK, (review_token or "")[:24]))])

        return InlineKeyboardMarkup(rows)
    except Exception as e:
        logger.exception("Failed to build channel picker keyboard: %s", e)
        # Fallback: minimal keyboard to avoid crashes
        return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.BACK, (review_token or "")[:24]))]])
# --- END OF FILE ---