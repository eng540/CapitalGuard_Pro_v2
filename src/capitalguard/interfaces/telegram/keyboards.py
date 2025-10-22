# src/capitalguard/interfaces/telegram/keyboards.py (v21.7 - Final Exit Management UI)
"""
Builds all Telegram keyboards for the bot.
✅ NEW: Implements the new unified Exit Management control panel and all its sub-panels.
✅ REFINED: All callback data now uses the unified CallbackBuilder for maximum reliability.
This is the final, production-ready version for the new feature.
"""

import math
import logging
from decimal import Decimal
from typing import List, Iterable, Set, Optional, Any, Dict, Tuple, Union
from enum import Enum

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from capitalguard.domain.entities import Recommendation, RecommendationStatus
from capitalguard.application.services.price_service import PriceService
from capitalguard.interfaces.telegram.ui_texts import _pct

logger = logging.getLogger(__name__)
ITEMS_PER_PAGE = 8
MAX_BUTTON_TEXT_LENGTH = 40
MAX_CALLBACK_DATA_LENGTH = 64

# --- Core Callback Architecture ---

class CallbackNamespace(Enum):
    POSITION = "pos"
    RECOMMENDATION = "rec"
    EXIT_STRATEGY = "exit"
    NAVIGATION = "nav"
    PUBLICATION = "pub"
    FORWARD_PARSE = "fwd_parse"
    FORWARD_CONFIRM = "fwd_confirm"

class CallbackAction(Enum):
    SHOW = "sh"
    UPDATE = "up"
    NAVIGATE = "nv"
    BACK = "bk"
    CLOSE = "cl"
    PARTIAL = "pt"
    CONFIRM = "cf"
    CANCEL = "cn"

class CallbackBuilder:
    @staticmethod
    def create(namespace: Union[CallbackNamespace, str], action: Union[CallbackAction, str], *params) -> str:
        ns_val = namespace.value if isinstance(namespace, CallbackNamespace) else namespace
        act_val = action.value if isinstance(action, CallbackAction) else action
        param_str = ":".join(map(str, params))
        base = f"{ns_val}:{act_val}"
        if param_str: base = f"{base}:{param_str}"
        if len(base) > MAX_CALLBACK_DATA_LENGTH:
            logger.warning(f"Callback data truncated: {base}")
            return base[:MAX_CALLBACK_DATA_LENGTH]
        return base

    @staticmethod
    def parse(callback_data: str) -> Dict[str, Any]:
        try:
            parts = callback_data.split(':')
            return {'raw': callback_data, 'namespace': parts[0] if parts else None, 'action': parts[1] if len(parts) > 1 else None, 'params': parts[2:] if len(parts) > 2 else []}
        except Exception:
            return {'raw': callback_data, 'error': 'Parsing failed'}

# --- UI Constants and Helpers ---

class StatusIcons:
    PENDING = "⏳"; ACTIVE = "▶️"; PROFIT = "🟢"; LOSS = "🔴"; CLOSED = "🏁"; ERROR = "⚠️"

class ButtonTexts:
    BACK_TO_LIST = "⬅️ العودة للقائمة"; BACK_TO_MAIN = "⬅️ العودة للوحة التحكم"; PREVIOUS = "⬅️ السابق"; NEXT = "التالي ➡️"

def _get_attr(obj: Any, attr: str, default: Any = None) -> Any:
    val = getattr(obj, attr, default)
    return val.value if hasattr(val, 'value') else val

def _truncate_text(text: str, max_length: int = MAX_BUTTON_TEXT_LENGTH) -> str:
    return text if len(text) <= max_length else text[:max_length-3] + "..."

class StatusDeterminer:
    @staticmethod
    def determine_icon(item: Any, live_price: Optional[float] = None) -> str:
        try:
            status = _get_attr(item, 'status')
            if status in [RecommendationStatus.PENDING, 'PENDING']: return StatusIcons.PENDING
            if status in [RecommendationStatus.CLOSED, 'CLOSED']: return StatusIcons.CLOSED
            if status in [RecommendationStatus.ACTIVE, 'ACTIVE', 'OPEN']:
                if live_price is not None:
                    entry = float(_get_attr(item, 'entry', 0))
                    side = _get_attr(item, 'side')
                    if entry > 0:
                        pnl = _pct(entry, live_price, side)
                        return StatusIcons.PROFIT if pnl >= 0 else StatusIcons.LOSS
                return StatusIcons.ACTIVE
            return StatusIcons.ERROR
        except Exception: return StatusIcons.ERROR

class NavigationBuilder:
    @staticmethod
    def build_pagination(current_page: int, total_pages: int) -> List[List[InlineKeyboardButton]]:
        buttons = []
        if current_page > 1: buttons.append(InlineKeyboardButton(ButtonTexts.PREVIOUS, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, current_page - 1)))
        if total_pages > 1: buttons.append(InlineKeyboardButton(f"{current_page}/{total_pages}", callback_data="noop"))
        if current_page < total_pages: buttons.append(InlineKeyboardButton(ButtonTexts.NEXT, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, current_page + 1)))
        return [buttons] if buttons else []

# --- Keyboard Factories ---

def analyst_control_panel_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    """The unified control panel for active recommendations."""
    rec_id = rec.id
    keyboard = [
        [
            InlineKeyboardButton("🔄 تحديث السعر", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id)),
            InlineKeyboardButton("💰 إغلاق جزئي", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "partial_close_menu", rec_id)),
            InlineKeyboardButton("❌ إغلاق كلي", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "close_menu", rec_id)),
        ],
        [InlineKeyboardButton("📈 إدارة الخروج والمخاطر", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "show_menu", rec_id))],
        [InlineKeyboardButton("✏️ تعديل بيانات الصفقة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_menu", rec_id))],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1))],
    ]
    return InlineKeyboardMarkup(keyboard)

def build_exit_management_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    """The exit strategy management panel."""
    rec_id = rec.id
    keyboard = [
        [InlineKeyboardButton("⚖️ نقل الوقف إلى التعادل (فوري)", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "move_to_be", rec_id))],
        [InlineKeyboardButton("🔒 تفعيل حجز ربح ثابت", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "set_fixed", rec_id))],
        [InlineKeyboardButton("📈 تفعيل الوقف المتحرك", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "set_trailing", rec_id))],
    ]
    if rec.profit_stop_active:
        keyboard.append([InlineKeyboardButton("❌ إلغاء الاستراتيجية الآلية", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "cancel", rec_id))])
    
    keyboard.append([InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))])
    return InlineKeyboardMarkup(keyboard)

def build_trade_data_edit_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    """The trade data editing panel."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 تعديل سعر الدخول", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_entry", rec_id))],
        [InlineKeyboardButton("🛑 تعديل وقف الخسارة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_sl", rec_id))],
        [InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_tp", rec_id))],
        [InlineKeyboardButton("📝 تعديل الملاحظات", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_notes", rec_id))],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))],
    ])

# --- Other Keyboards ---

async def build_open_recs_keyboard(items: List[Any], current_page: int, price_service: PriceService) -> InlineKeyboardMarkup:
    try:
        total_items = len(items)
        total_pages = math.ceil(total_items / ITEMS_PER_PAGE) or 1
        start_index = (current_page - 1) * ITEMS_PER_PAGE
        paginated_items = items[start_index:start_index + ITEMS_PER_PAGE]
        prices_map = {_get_attr(item, 'asset'): await price_service.get_cached_price(_get_attr(item, 'asset'), _get_attr(item, 'market', 'Futures')) for item in paginated_items}
        keyboard_rows = []
        for item in paginated_items:
            rec_id, asset, side = _get_attr(item, 'id'), _get_attr(item, 'asset'), _get_attr(item, 'side')
            live_price = prices_map.get(asset)
            status_icon = StatusDeterminer.determine_icon(item, live_price)
            button_text = f"#{rec_id} - {asset} ({side})"
            if live_price is not None and status_icon in [StatusIcons.PROFIT, StatusIcons.LOSS]:
                pnl = _pct(float(_get_attr(item, 'entry', 0)), live_price, side)
                button_text = f"{status_icon} {button_text} | PnL: {pnl:+.2f}%"
            else:
                button_text = f"{status_icon} {button_text}"
            item_type = 'trade' if getattr(item, 'is_user_trade', False) else 'rec'
            callback_data = CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, item_type, rec_id)
            keyboard_rows.append([InlineKeyboardButton(_truncate_text(button_text), callback_data=callback_data)])
        keyboard_rows.extend(NavigationBuilder.build_pagination(current_page, total_pages))
        return InlineKeyboardMarkup(keyboard_rows)
    except Exception as e:
        logger.error(f"Open recs keyboard build failed: {e}", exc_info=True)
        return InlineKeyboardMarkup([[InlineKeyboardButton("⚠️ خطأ في تحميل البيانات", callback_data="noop")]])

def build_close_options_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📉 إغلاق بسعر السوق", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "close_market", rec_id))],
        [InlineKeyboardButton("✍️ إغلاق بسعر محدد", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "close_manual", rec_id))],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))],
    ])

def build_partial_close_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 إغلاق 25%", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.PARTIAL, rec_id, "25"))],
        [InlineKeyboardButton("💰 إغلاق 50%", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.PARTIAL, rec_id, "50"))],
        [InlineKeyboardButton("✍️ نسبة مخصصة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "partial_close_custom", rec_id))],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))],
    ])

def build_confirmation_keyboard(namespace: str, item_id: int, confirm_text: str = "✅ Confirm", cancel_text: str = "❌ Cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(confirm_text, callback_data=CallbackBuilder.create(namespace, CallbackAction.CONFIRM, item_id)),
        InlineKeyboardButton(cancel_text, callback_data=CallbackBuilder.create(namespace, CallbackAction.CANCEL, item_id)),
    ]])

def public_channel_keyboard(rec_id: int, bot_username: Optional[str]) -> InlineKeyboardMarkup:
    buttons = []
    if bot_username:
        buttons.append(InlineKeyboardButton("📊 تتبّع الإشارة", url=f"https://t.me/{bot_username}?start=track_{rec_id}"))
    return InlineKeyboardMarkup([buttons])

def build_user_trade_control_keyboard(trade_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث السعر", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.UPDATE, "trade", trade_id)), InlineKeyboardButton("❌ إغلاق الصفقة", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.CLOSE, "trade", trade_id))],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1))],
    ])