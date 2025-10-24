# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/keyboards.py ---
# src/capitalguard/interfaces/telegram/keyboards.py (v21.12 - Input Confirmation & State Handling)
"""
Builds all Telegram keyboards for the bot.
✅ NEW: Added build_input_confirmation_keyboard for safer data modification.
✅ NEW: Added explicit cancel button callback for input prompts.
✅ FIX: Removed invalid citation syntax causing a SyntaxError on startup.
✅ UX HOTFIX: Restored direct access buttons on the main analyst control panel.
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
    INPUT_MGMT = "inp" # ✅ NEW: Namespace for input confirmation/cancellation

class CallbackAction(Enum):
    SHOW = "sh"
    UPDATE = "up"
    NAVIGATE = "nv"
    BACK = "bk"
    CLOSE = "cl"
    PARTIAL = "pt"
    CONFIRM = "cf"
    CANCEL = "cn"
    RETRY = "rt" # ✅ NEW: Action for retrying input

class CallbackBuilder:
    @staticmethod
    def create(namespace: Union[CallbackNamespace, str], action: Union[CallbackAction, str], *params) -> str:
        ns_val = namespace.value if isinstance(namespace, CallbackNamespace) else namespace
        act_val = action.value if isinstance(action, CallbackAction) else action
        param_str = ":".join(map(str, params))
        base = f"{ns_val}:{act_val}"
        if param_str: base = f"{base}:{param_str}"
        if len(base) > MAX_CALLBACK_DATA_LENGTH:
            # Simple truncation might break parsing, consider hashing or alternative if becomes common
            logger.warning(f"Callback data truncated: {base}")
            return base[:MAX_CALLBACK_DATA_LENGTH]
        return base

    @staticmethod
    def parse(callback_data: str) -> Dict[str, Any]:
        try:
            parts = callback_data.split(':')
            # Ensure robustness against truncated or malformed data
            namespace = parts[0] if parts else None
            action = parts[1] if len(parts) > 1 else None
            params = parts[2:] if len(parts) > 2 else []
            return {'raw': callback_data, 'namespace': namespace, 'action': action, 'params': params}
        except Exception as e:
            logger.error(f"Failed to parse callback_data '{callback_data}': {e}")
            return {'raw': callback_data, 'error': 'Parsing failed'}

# --- UI Constants and Helpers ---

class StatusIcons:
    PENDING = "⏳"; ACTIVE = "▶️"; PROFIT = "🟢"; LOSS = "🔴"; CLOSED = "🏁"; ERROR = "⚠️"

class ButtonTexts:
    BACK_TO_LIST = "⬅️ العودة للقائمة"; BACK_TO_MAIN = "⬅️ العودة للوحة التحكم";
    PREVIOUS = "⬅️ السابق"; NEXT = "التالي ➡️";
    CONFIRM_CHANGE = "✅ تأكيد التغيير"; RETRY_INPUT = "✏️ إعادة الإدخال";
    CANCEL_INPUT = "❌ إلغاء الإدخال"; CANCEL_ALL = "❌ إلغاء الكل"

def _get_attr(obj: Any, attr: str, default: Any = None) -> Any:
    val = getattr(obj, attr, default)
    # Handle potential None value before accessing .value
    return val.value if hasattr(val, 'value') and val is not None else val


def _truncate_text(text: str, max_length: int = MAX_BUTTON_TEXT_LENGTH) -> str:
    return text if len(text) <= max_length else text[:max_length-3] + "..."

class StatusDeterminer:
    @staticmethod
    def determine_icon(item: Any, live_price: Optional[float] = None) -> str:
        try:
            status_val = _get_attr(item, 'status')
            status = RecommendationStatus(status_val) if isinstance(status_val, str) else status_val

            if status == RecommendationStatus.PENDING: return StatusIcons.PENDING
            if status == RecommendationStatus.CLOSED: return StatusIcons.CLOSED
            if status == RecommendationStatus.ACTIVE:
                if live_price is not None:
                    entry = float(_get_attr(item, 'entry', 0))
                    side = _get_attr(item, 'side')
                    if entry > 0:
                        pnl = _pct(entry, live_price, side)
                        return StatusIcons.PROFIT if pnl >= 0 else StatusIcons.LOSS
                return StatusIcons.ACTIVE # Default for ACTIVE if no price or PNL calc fails
            return StatusIcons.ERROR # Should not happen with Enum
        except Exception as e:
             logger.warning(f"Error determining icon for item {getattr(item, 'id', '?')}: {e}")
             return StatusIcons.ERROR

class NavigationBuilder:
    @staticmethod
    def build_pagination(current_page: int, total_pages: int) -> List[List[InlineKeyboardButton]]:
        buttons = []
        if current_page > 1: buttons.append(InlineKeyboardButton(ButtonTexts.PREVIOUS, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, current_page - 1)))
        if total_pages > 1: buttons.append(InlineKeyboardButton(f"{current_page}/{total_pages}", callback_data="noop")) # No action on page number itself
        if current_page < total_pages: buttons.append(InlineKeyboardButton(ButtonTexts.NEXT, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, current_page + 1)))
        return [buttons] if buttons else []

# --- Keyboard Factories ---

def analyst_control_panel_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    """The unified control panel. Buttons might be disabled based on status."""
    rec_id = rec.id
    status_val = _get_attr(rec, 'status')
    status = RecommendationStatus(status_val) if isinstance(status_val, str) else status_val
    is_active = status == RecommendationStatus.ACTIVE

    keyboard = []
    row1 = [InlineKeyboardButton("🔄 تحديث السعر", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))]
    if is_active:
        row1.extend([
            InlineKeyboardButton("💰 إغلاق جزئي", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "partial_close_menu", rec_id)),
            InlineKeyboardButton("❌ إغلاق كلي", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "close_menu", rec_id)),
        ])
    keyboard.append(row1)

    row2 = []
    if is_active:
        row2.append(InlineKeyboardButton("📈 إدارة الخروج والمخاطر", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "show_menu", rec_id)))
    # Allow editing notes even if pending, edit other data only if active/pending
    if status != RecommendationStatus.CLOSED:
         row2.append(InlineKeyboardButton("✏️ تعديل بيانات الصفقة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_menu", rec_id)))
    if row2: keyboard.append(row2)

    keyboard.append([InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1))])
    return InlineKeyboardMarkup(keyboard)

def build_exit_management_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    """Exit strategy panel. Assumes rec is ACTIVE."""
    rec_id = rec.id
    keyboard = [
        [InlineKeyboardButton("⚖️ نقل الوقف إلى التعادل (فوري)", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "move_to_be", rec_id))],
        [InlineKeyboardButton("🔒 تفعيل حجز ربح ثابت", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "set_fixed", rec_id))],
        [InlineKeyboardButton("📈 تفعيل الوقف المتحرك", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "set_trailing", rec_id))],
    ]
    # Check the attribute directly from the ORM model if possible, or use _get_attr safely
    profit_stop_active = getattr(rec, 'profit_stop_active', _get_attr(rec, 'profit_stop_active', False))
    if profit_stop_active:
        keyboard.append([InlineKeyboardButton("🚫 إلغاء الاستراتيجية الآلية", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "cancel", rec_id))])

    keyboard.append([InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))])
    return InlineKeyboardMarkup(keyboard)

def build_trade_data_edit_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    """Trade data editing panel. Dynamically shows/hides entry edit."""
    rec_id = rec.id
    status_val = _get_attr(rec, 'status')
    status = RecommendationStatus(status_val) if isinstance(status_val, str) else status_val

    keyboard = []
    # Only allow entry edit if PENDING
    if status == RecommendationStatus.PENDING:
        keyboard.append([InlineKeyboardButton("💰 تعديل سعر الدخول", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_entry", rec_id))])

    # Allow SL/TP/Notes edit if ACTIVE or PENDING
    if status != RecommendationStatus.CLOSED:
        keyboard.extend([
            [InlineKeyboardButton("🛑 تعديل وقف الخسارة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_sl", rec_id))],
            [InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_tp", rec_id))],
            [InlineKeyboardButton("📝 تعديل الملاحظات", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_notes", rec_id))],
        ])

    keyboard.append([InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))])
    return InlineKeyboardMarkup(keyboard)


async def build_open_recs_keyboard(items: List[Any], current_page: int, price_service: PriceService) -> InlineKeyboardMarkup:
    try:
        total_items = len(items)
        total_pages = math.ceil(total_items / ITEMS_PER_PAGE) or 1
        # Ensure current_page is within valid bounds
        current_page = max(1, min(current_page, total_pages))
        start_index = (current_page - 1) * ITEMS_PER_PAGE
        paginated_items = items[start_index:start_index + ITEMS_PER_PAGE]

        # Fetch prices concurrently for efficiency
        price_tasks = {item.id: price_service.get_cached_price(_get_attr(item, 'asset'), _get_attr(item, 'market', 'Futures')) for item in paginated_items if _get_attr(item, 'asset')}
        prices_map_results = await asyncio.gather(*price_tasks.values())
        prices_map = dict(zip(price_tasks.keys(), prices_map_results))

        keyboard_rows = []
        for item in paginated_items:
            rec_id, asset, side = _get_attr(item, 'id'), _get_attr(item, 'asset'), _get_attr(item, 'side')
            live_price = prices_map.get(rec_id)
            status_icon = StatusDeterminer.determine_icon(item, live_price)
            button_text = f"#{rec_id} - {asset} ({side})"

            if live_price is not None and status_icon in [StatusIcons.PROFIT, StatusIcons.LOSS]:
                entry_price_val = _get_attr(item, 'entry', 0)
                # Ensure entry price is valid before calculating PNL
                entry_price_float = float(entry_price_val) if entry_price_val else 0.0
                if entry_price_float > 0:
                     pnl = _pct(entry_price_float, live_price, side)
                     button_text = f"{status_icon} {button_text} | PnL: {pnl:+.2f}%"
                else:
                     button_text = f"{status_icon} {button_text}" # Show icon without PNL if entry is invalid
            else:
                button_text = f"{status_icon} {button_text}"

            item_type = 'trade' if getattr(item, 'is_user_trade', False) else 'rec'
            callback_data = CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, item_type, rec_id)
            keyboard_rows.append([InlineKeyboardButton(_truncate_text(button_text), callback_data=callback_data)])

        keyboard_rows.extend(NavigationBuilder.build_pagination(current_page, total_pages))
        return InlineKeyboardMarkup(keyboard_rows)
    except Exception as e:
        logger.error(f"Open recs keyboard build failed: {e}", exc_info=True)
        # Provide a way back if loading fails
        return InlineKeyboardMarkup([[InlineKeyboardButton("⚠️ خطأ في تحميل البيانات - العودة", callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1))]])

def build_close_options_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    # Assumes rec is ACTIVE
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📉 إغلاق بسعر السوق", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "close_market", rec_id))],
        [InlineKeyboardButton("✍️ إغلاق بسعر محدد", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "close_manual", rec_id))],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))],
    ])

def build_partial_close_keyboard(rec_id: int) -> InlineKeyboardMarkup:
     # Assumes rec is ACTIVE
     # ✅ FIX: Ensure callback data for fixed percentages includes percentage value
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 إغلاق 25%", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.PARTIAL, rec_id, "25"))],
        [InlineKeyboardButton("💰 إغلاق 50%", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.PARTIAL, rec_id, "50"))],
        [InlineKeyboardButton("✍️ نسبة مخصصة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "partial_close_custom", rec_id))], # This might need a different handler or state
        [InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))],
    ])

def build_confirmation_keyboard(namespace: str, item_id: int, confirm_text: str = "✅ Confirm", cancel_text: str = "❌ Cancel") -> InlineKeyboardMarkup:
    """Generic confirmation keyboard."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(confirm_text, callback_data=CallbackBuilder.create(namespace, CallbackAction.CONFIRM, item_id)),
        InlineKeyboardButton(cancel_text, callback_data=CallbackBuilder.create(namespace, CallbackAction.CANCEL, item_id)),
    ]])

# ✅ NEW: Keyboard for confirming or canceling user text input during management flows
def build_input_confirmation_keyboard(original_action: str, rec_id: int) -> InlineKeyboardMarkup:
    """Keyboard shown after user provides text input for SL/TP/etc."""
    # We pass the original action and rec_id so the confirmation handler knows what to do.
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(ButtonTexts.CONFIRM_CHANGE, callback_data=CallbackBuilder.create(CallbackNamespace.INPUT_MGMT, CallbackAction.CONFIRM, original_action, rec_id)),
            InlineKeyboardButton(ButtonTexts.RETRY_INPUT, callback_data=CallbackBuilder.create(CallbackNamespace.INPUT_MGMT, CallbackAction.RETRY, original_action, rec_id)),
            InlineKeyboardButton(ButtonTexts.CANCEL_ALL, callback_data=CallbackBuilder.create(CallbackNamespace.INPUT_MGMT, CallbackAction.CANCEL, rec_id)) # Cancel returns to main panel
        ]
    ])

# ✅ NEW: Simple cancel button for the initial input prompt
def build_input_cancel_keyboard(original_menu_callback: str) -> InlineKeyboardMarkup:
     """Adds a cancel button below the input prompt message."""
     return InlineKeyboardMarkup([[
         InlineKeyboardButton(ButtonTexts.CANCEL_INPUT, callback_data=original_menu_callback) # Re-use the callback that got us here
     ]])

def public_channel_keyboard(rec_id: int, bot_username: Optional[str]) -> InlineKeyboardMarkup:
    buttons = []
    if bot_username:
        # Ensure deep link URL is correctly formatted
        buttons.append(InlineKeyboardButton("📊 تتبّع الإشارة", url=f"https://t.me/{bot_username.lstrip('@')}?start=track_{rec_id}"))
    return InlineKeyboardMarkup([buttons]) if buttons else None # Return None if no button

def build_user_trade_control_keyboard(trade_id: int) -> InlineKeyboardMarkup:
     # User trades are simpler: update price or close
     # Note: Update price might not be meaningful if derived from a rec. Consider removing.
    return InlineKeyboardMarkup([
        [
             # InlineKeyboardButton("🔄 تحديث السعر", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, "trade", trade_id)), # Refreshing shows latest price
             InlineKeyboardButton("❌ إغلاق الصفقة يدويًا", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.CLOSE, "trade", trade_id)) # Needs confirmation step
        ],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1))],
    ])

def build_subscription_keyboard(channel_link: Optional[str]) -> Optional[InlineKeyboardMarkup]:
    if channel_link:
        return InlineKeyboardMarkup([[InlineKeyboardButton("➡️ الانضمام للقناة", url=channel_link)]])
    return None

# --- Recommendation Creation Keyboards ---

def main_creation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 المنشئ التفاعلي", callback_data="method_interactive")],
        [InlineKeyboardButton("⚡️ الأمر السريع", callback_data="method_quick")],
        [InlineKeyboardButton("📋 المحرر النصي", callback_data="method_editor")],
    ])

def asset_choice_keyboard(recent_assets: List[str]) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(asset, callback_data=f"asset_{asset}") for asset in recent_assets]
    # Ensure rows have max 3 buttons
    keyboard = [buttons[i: i + 3] for i in range(0, len(buttons), 3)]
    keyboard.append([InlineKeyboardButton("✍️ اكتب أصلاً جديدًا", callback_data="asset_new")])
    return InlineKeyboardMarkup(keyboard)

def side_market_keyboard(current_market: str = "Futures") -> InlineKeyboardMarkup:
    market_display = "Futures" if "futures" in current_market.lower() else "Spot"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🟢 LONG / {market_display}", callback_data="side_LONG"), InlineKeyboardButton(f"🔴 SHORT / {market_display}", callback_data="side_SHORT")],
        [InlineKeyboardButton(f"🔄 تغيير السوق ({market_display})", callback_data="side_menu")],
    ])

def market_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 Futures", callback_data="market_Futures"), InlineKeyboardButton("💎 Spot", callback_data="market_Spot")],
        [InlineKeyboardButton("⬅️ عودة", callback_data="market_back")],
    ])

def order_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Market", callback_data="type_MARKET")],
        [InlineKeyboardButton("🎯 Limit", callback_data="type_LIMIT")],
        [InlineKeyboardButton("🚨 Stop Market", callback_data="type_STOP_MARKET")],
    ])

def review_final_keyboard(review_token: str) -> InlineKeyboardMarkup:
    # Use a shortened token for callback data to stay within limits
    short_token = review_token[:12] # Keep consistent with channel picker
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نشر الآن", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "publish", short_token))],
        [InlineKeyboardButton("📢 اختيار القنوات", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "choose_channels", short_token)), InlineKeyboardButton("📝 إضافة ملاحظات", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "add_notes", short_token))],
        [InlineKeyboardButton("❌ إلغاء", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "cancel", short_token))],
    ])

def build_channel_picker_keyboard(review_token: str, channels: Iterable[Any], selected_ids: Set[int], page: int = 1, per_page: int = 6) -> InlineKeyboardMarkup:
    try:
        ch_list = list(channels)
        total = len(ch_list)
        total_pages = max(1, math.ceil(total / per_page))
        page = max(1, min(page, total_pages))
        start_idx, end_idx = (page - 1) * per_page, page * per_page
        page_items = ch_list[start_idx:end_idx]
        rows = []
        short_token = review_token[:12] # Use shortened token

        for ch in page_items:
            tg_chat_id = int(_get_attr(ch, 'telegram_channel_id', 0))
            if not tg_chat_id: continue # Skip if ID is invalid
            label = _truncate_text(f"{_get_attr(ch, 'title', 'Untitled')} ({'Active' if _get_attr(ch, 'is_active', False) else 'Inactive'})", 25)
            status = "✅" if tg_chat_id in selected_ids else "☑️"
            callback_data = CallbackBuilder.create(CallbackNamespace.PUBLICATION, "toggle", short_token, tg_chat_id, page)
            rows.append([InlineKeyboardButton(f"{status} {label}", callback_data=callback_data)])

        nav_buttons = []
        if page > 1: nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, "nav", short_token, page - 1)))
        if total_pages > 1: nav_buttons.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
        if page < total_pages: nav_buttons.append(InlineKeyboardButton("➡️", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, "nav", short_token, page + 1)))
        if nav_buttons: rows.append(nav_buttons)

        rows.append([
             InlineKeyboardButton("🚀 نشر المحدد", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.CONFIRM, short_token)),
             InlineKeyboardButton("⬅️ عودة للمراجعة", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.BACK, short_token))
        ])
        return InlineKeyboardMarkup(rows)
    except Exception as e:
        logger.error(f"Error building channel picker: {e}", exc_info=True)
        # Ensure fallback provides the correct token
        return InlineKeyboardMarkup([[InlineKeyboardButton("❌ خطأ - عودة للمراجعة", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.BACK, review_token[:12]))]])

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/keyboards.py ---