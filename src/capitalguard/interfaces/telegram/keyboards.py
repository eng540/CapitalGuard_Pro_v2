# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/keyboards.py ---
# src/capitalguard/interfaces/telegram/keyboards.py (v21.12 - Confirmation & Dynamic Buttons)
"""
Builds all Telegram keyboards for the bot.
✅ FIX: Added build_input_confirmation_keyboard.
✅ FIX: Adjusted build_trade_data_edit_keyboard to potentially hide buttons based on status.
✅ FIX: Removed invalid citation syntax causing a SyntaxError on startup.
✅ UX HOTFIX: Restored direct access to "Partial Close" and "Full Close" buttons.
- Implements the new unified Exit Management control panel.
- All callback data now uses the unified CallbackBuilder.
"""
import asyncio
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
    # ✅ FIX: Added namespace for input confirmation
    INPUT_CONFIRM = "inp_cf"

class CallbackAction(Enum):
    SHOW = "sh"
    UPDATE = "up"
    NAVIGATE = "nv"
    BACK = "bk"
    CLOSE = "cl"
    PARTIAL = "pt"
    CONFIRM = "cf"
    CANCEL = "cn"
    # ✅ FIX: Added action for re-entering input
    RETRY_INPUT = "rt"

class CallbackBuilder:
    @staticmethod
    def create(namespace: Union[CallbackNamespace, str], action: Union[CallbackAction, str], *params) -> str:
        ns_val = namespace.value if isinstance(namespace, CallbackNamespace) else namespace
        act_val = action.value if isinstance(action, CallbackAction) else action
        param_str = ":".join(map(str, params))
        base = f"{ns_val}:{act_val}"
        if param_str: base = f"{base}:{param_str}"
        if len(base) > MAX_CALLBACK_DATA_LENGTH:
            # Shorten params if too long? For now, just truncate.
            logger.warning(f"Callback data longer than 64 bytes, truncating: {base}")
            safe_params_str = ":".join(map(str, params))[:MAX_CALLBACK_DATA_LENGTH - len(f"{ns_val}:{act_val}:")]
            base = f"{ns_val}:{act_val}:{safe_params_str}".rstrip(":")
            # Recheck length after attempting to shorten params
            if len(base) > MAX_CALLBACK_DATA_LENGTH:
                 base = base[:MAX_CALLBACK_DATA_LENGTH] # Final hard truncate if still too long
        return base

    @staticmethod
    def parse(callback_data: str) -> Dict[str, Any]:
        try:
            parts = callback_data.split(':')
            return {'raw': callback_data, 'namespace': parts[0] if parts else None, 'action': parts[1] if len(parts) > 1 else None, 'params': parts[2:] if len(parts) > 2 else []}
        except Exception:
            logger.error(f"Failed to parse callback_data: {callback_data}", exc_info=True)
            return {'raw': callback_data, 'error': 'Parsing failed'}

# --- UI Constants and Helpers (Unchanged) ---

class StatusIcons:
    PENDING = "⏳";
    ACTIVE = "▶️"; PROFIT = "🟢"; LOSS = "🔴"; CLOSED = "🏁";
    ERROR = "⚠️"

class ButtonTexts:
    BACK_TO_LIST = "⬅️ العودة للقائمة"; BACK_TO_MAIN = "⬅️ العودة للوحة التحكم";
    PREVIOUS = "⬅️ السابق"; NEXT = "التالي ➡️"

def _get_attr(obj: Any, attr: str, default: Any = None) -> Any:
    val = getattr(obj, attr, default)
    # Handle cases where val itself might be an Enum or have a .value
    if hasattr(val, 'value'):
        return val.value
    # Special handling for Price/Symbol/Side objects if needed, although direct access might be intended
    # if isinstance(val, (Price, Symbol, Side)): return val.value # Uncomment if needed
    return val

def _truncate_text(text: str, max_length: int = MAX_BUTTON_TEXT_LENGTH) -> str:
    return text if len(text) <= max_length else text[:max_length-3] + "..."

class StatusDeterminer:
    @staticmethod
    def determine_icon(item: Any, live_price: Optional[float] = None) -> str:
        try:
            status_val = _get_attr(item, 'status')
            # Ensure comparison works with both Enum members and string values
            if status_val == RecommendationStatus.PENDING or status_val == 'PENDING': return StatusIcons.PENDING
            if status_val == RecommendationStatus.CLOSED or status_val == 'CLOSED': return StatusIcons.CLOSED
            if status_val == RecommendationStatus.ACTIVE or status_val == 'ACTIVE' or status_val == 'OPEN':
                if live_price is not None:
                    # Safely get entry and side, converting Price/Side objects if necessary
                    entry_raw = getattr(item, 'entry', 0)
                    entry = float(entry_raw.value) if hasattr(entry_raw, 'value') else float(entry_raw)
                    side_raw = getattr(item, 'side', '')
                    side = side_raw.value if hasattr(side_raw, 'value') else str(side_raw)

                    if entry > 0:
                        pnl = _pct(entry, live_price, side)
                        return StatusIcons.PROFIT if pnl >= 0 else StatusIcons.LOSS
                return StatusIcons.ACTIVE # Default for ACTIVE if price fails or entry is 0
            logger.warning(f"Unknown status value for icon determination: {status_val}")
            return StatusIcons.ERROR # Fallback for unknown status
        except Exception as e:
             logger.error(f"Error determining status icon: {e}", exc_info=True)
             return StatusIcons.ERROR

class NavigationBuilder:
    @staticmethod
    def build_pagination(current_page: int, total_pages: int) -> List[List[InlineKeyboardButton]]:
        buttons = []
        if current_page > 1: buttons.append(InlineKeyboardButton(ButtonTexts.PREVIOUS, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, current_page - 1)))
        if total_pages > 1: buttons.append(InlineKeyboardButton(f"{current_page}/{total_pages}", callback_data="noop")) # No action button
        if current_page < total_pages: buttons.append(InlineKeyboardButton(ButtonTexts.NEXT, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, current_page + 1)))
        return [buttons] if buttons else []

# --- Keyboard Factories ---

def analyst_control_panel_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    """The unified control panel, shows buttons based on status."""
    rec_id = rec.id
    status = _get_attr(rec, 'status')
    keyboard = []

    # Row 1: Always show refresh, conditional close options
    row1 = [InlineKeyboardButton("🔄 تحديث السعر", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))]
    if status == RecommendationStatus.ACTIVE:
        row1.append(InlineKeyboardButton("💰 إغلاق جزئي", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "partial_close_menu", rec_id)))
        row1.append(InlineKeyboardButton("❌ إغلاق كلي", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "close_menu", rec_id)))
    keyboard.append(row1)

    # Row 2: Conditional management options
    row2 = []
    if status == RecommendationStatus.ACTIVE:
        row2.append(InlineKeyboardButton("📈 إدارة الخروج والمخاطر", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "show_menu", rec_id)))
        row2.append(InlineKeyboardButton("✏️ تعديل بيانات الصفقة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_menu", rec_id)))
    # Allow editing notes even if PENDING? Assuming yes for now.
    elif status == RecommendationStatus.PENDING:
         row2.append(InlineKeyboardButton("✏️ تعديل بيانات الصفقة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_menu", rec_id)))
    if row2:
        keyboard.append(row2)

    # Row 3: Always show back to list
    keyboard.append([InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1))])

    return InlineKeyboardMarkup(keyboard)


def build_exit_management_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    """The exit strategy management panel."""
    rec_id = rec.id
    keyboard = [
        [InlineKeyboardButton("⚖️ نقل الوقف إلى التعادل (فوري)", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "move_to_be", rec_id))],
        [InlineKeyboardButton("🔒 تفعيل حجز ربح ثابت", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "set_fixed", rec_id))],
        [InlineKeyboardButton("📈 تفعيل الوقف المتحرك", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "set_trailing", rec_id))],
    ]
    # Check the attribute directly if it exists, default to False
    if getattr(rec, 'profit_stop_active', False):
        keyboard.append([InlineKeyboardButton("❌ إلغاء الاستراتيجية الآلية", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "cancel", rec_id))])

    keyboard.append([InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))])
    return InlineKeyboardMarkup(keyboard)

def build_trade_data_edit_keyboard(rec_id: int, status: RecommendationStatus) -> InlineKeyboardMarkup:
    """The trade data editing panel, buttons shown based on status."""
    keyboard = []
    # ✅ FIX: Conditionally show edit buttons based on recommendation status
    if status == RecommendationStatus.PENDING:
        keyboard.append([InlineKeyboardButton("💰 تعديل سعر الدخول", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_entry", rec_id))])
    if status == RecommendationStatus.ACTIVE:
        keyboard.append([InlineKeyboardButton("🛑 تعديل وقف الخسارة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_sl", rec_id))])
        keyboard.append([InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_tp", rec_id))])
    # Allow editing notes for both PENDING and ACTIVE
    if status in [RecommendationStatus.PENDING, RecommendationStatus.ACTIVE]:
        keyboard.append([InlineKeyboardButton("📝 تعديل الملاحظات", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_notes", rec_id))])

    keyboard.append([InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))])
    return InlineKeyboardMarkup(keyboard)


async def build_open_recs_keyboard(items: List[Any], current_page: int, price_service: PriceService) -> InlineKeyboardMarkup:
    """Builds the paginated list of open positions."""
    try:
        total_items = len(items)
        total_pages = math.ceil(total_items / ITEMS_PER_PAGE) or 1
        # Ensure current_page is within valid bounds
        current_page = max(1, min(current_page, total_pages))
        start_index = (current_page - 1) * ITEMS_PER_PAGE
        paginated_items = items[start_index:start_index + ITEMS_PER_PAGE]

        # Fetch prices concurrently for efficiency
        price_tasks = {
             item.id: price_service.get_cached_price(
                 _get_attr(item, 'asset'),
                 _get_attr(item, 'market', 'Futures')
             ) for item in paginated_items if hasattr(item, 'id')
        }
        prices_results = await asyncio.gather(*price_tasks.values(), return_exceptions=True)
        prices_map = {item_id: price for item_id, price in zip(price_tasks.keys(), prices_results) if isinstance(price, (float, int))}

        keyboard_rows = []
        for item in paginated_items:
            # Safely get attributes, handling potential missing values or different object types
            rec_id = _get_attr(item, 'id')
            asset = _get_attr(item, 'asset', 'N/A')
            side = _get_attr(item, 'side', 'N/A')
            if rec_id is None: continue # Skip items without an ID

            live_price = prices_map.get(rec_id)
            status_icon = StatusDeterminer.determine_icon(item, live_price)
            button_text = f"#{rec_id} - {asset} ({side})"

            if live_price is not None and status_icon in [StatusIcons.PROFIT, StatusIcons.LOSS]:
                 # Ensure entry is float for calculation
                 entry_raw = getattr(item, 'entry', 0)
                 entry = float(entry_raw.value) if hasattr(entry_raw, 'value') else float(entry_raw)
                 if entry > 0:
                      pnl = _pct(entry, live_price, side)
                      button_text = f"{status_icon} {button_text} | PnL: {pnl:+.2f}%"
                 else:
                      button_text = f"{status_icon} {button_text}" # Cannot calc PnL if entry is 0
            else:
                button_text = f"{status_icon} {button_text}"

            item_type = 'trade' if getattr(item, 'is_user_trade', False) else 'rec'
            callback_data = CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, item_type, rec_id)
            keyboard_rows.append([InlineKeyboardButton(_truncate_text(button_text), callback_data=callback_data)])

        keyboard_rows.extend(NavigationBuilder.build_pagination(current_page, total_pages))
        return InlineKeyboardMarkup(keyboard_rows)
    except Exception as e:
        logger.error(f"Open recs keyboard build failed: {e}", exc_info=True)
        # Provide a fallback keyboard indicating error
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ خطأ في تحميل البيانات", callback_data="noop")],
            [InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1))] # Allow navigation back
        ])


def build_close_options_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📉 إغلاق بسعر السوق", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "close_market", rec_id))],
        [InlineKeyboardButton("✍️ إغلاق بسعر محدد", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "close_manual", rec_id))],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))],
    ])

def build_partial_close_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    """Keyboard for partial close options."""
    return InlineKeyboardMarkup([
        # ✅ FIX: Buttons now trigger the partial_close_fixed action with percentage
        [InlineKeyboardButton("💰 إغلاق 25%", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "partial_close_fixed", rec_id, "25"))],
        [InlineKeyboardButton("💰 إغلاق 50%", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "partial_close_fixed", rec_id, "50"))],
        # Button for custom percentage still goes to a prompt
        [InlineKeyboardButton("✍️ نسبة مخصصة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "partial_close_custom", rec_id))],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))],
    ])

# ✅ FIX: New keyboard for input confirmation step
def build_input_confirmation_keyboard(confirm_callback: str, retry_callback: str, cancel_callback: str) -> InlineKeyboardMarkup:
    """Builds the confirmation keyboard after user input."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تأكيد التغيير", callback_data=confirm_callback),
            InlineKeyboardButton("✏️ إعادة الإدخال", callback_data=retry_callback),
        ],
        [InlineKeyboardButton("❌ إلغاء الكل", callback_data=cancel_callback)]
    ])

# ✅ FIX: New callback data generator for input cancellation button
def create_cancel_input_callback(original_menu_callback: str) -> str:
    """Creates callback data for cancelling input and returning to the previous menu."""
    # Assumes original_menu_callback is something like 'pos:sh:rec:123' or 'rec:edit_menu:123' etc.
    return CallbackBuilder.create(CallbackNamespace.INPUT_CONFIRM, CallbackAction.CANCEL, original_menu_callback)


def build_confirmation_keyboard(namespace: str, item_id: int, confirm_text: str = "✅ Confirm", cancel_text: str = "❌ Cancel") -> InlineKeyboardMarkup:
     """Generic confirmation keyboard."""
     # Ensure item_id is included for context if needed, adjust namespace/action as necessary
     # Example assumes confirmation/cancel apply directly to the item_id context.
     return InlineKeyboardMarkup([[
         InlineKeyboardButton(confirm_text, callback_data=CallbackBuilder.create(namespace, CallbackAction.CONFIRM, item_id)),
         InlineKeyboardButton(cancel_text, callback_data=CallbackBuilder.create(namespace, CallbackAction.CANCEL, item_id)),
     ]])


def public_channel_keyboard(rec_id: int, bot_username: Optional[str]) -> InlineKeyboardMarkup:
    buttons = []
    if bot_username:
        # Ensure the URL is correctly formatted
        track_url = f"https://t.me/{bot_username}?start=track_{rec_id}"
        buttons.append(InlineKeyboardButton("📊 تتبّع الإشارة", url=track_url))
    # Return keyboard even if button list is empty, or return None?
    # Returning empty keyboard is usually safer for PTB.
    return InlineKeyboardMarkup([buttons])


def build_user_trade_control_keyboard(trade_id: int) -> InlineKeyboardMarkup:
     # Assuming 'UPDATE' refreshes price, 'CLOSE' initiates close flow (needs handlers)
     return InlineKeyboardMarkup([
         [InlineKeyboardButton("🔄 تحديث السعر", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, "trade", trade_id)), # Re-show to update price
          InlineKeyboardButton("❌ إغلاق الصفقة", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.CLOSE, "trade", trade_id))], # Needs handler
         [InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1))],
     ])

def build_subscription_keyboard(channel_link: Optional[str]) -> Optional[InlineKeyboardMarkup]:
    if channel_link:
        # Ensure URL is valid before creating button
        if isinstance(channel_link, str) and channel_link.startswith(('http://', 'https://', 't.me/')):
             return InlineKeyboardMarkup([[InlineKeyboardButton("➡️ الانضمام للقناة", url=channel_link)]])
        else:
             logger.warning(f"Invalid channel link provided for subscription keyboard: {channel_link}")
             return None # Return None if link is invalid
    return None

def main_creation_keyboard() -> InlineKeyboardMarkup:
    # Uses simple strings as callback data, handled by conversation handler state logic
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 المنشئ التفاعلي", callback_data="method_interactive")],
        [InlineKeyboardButton("⚡️ الأمر السريع", callback_data="method_quick")],
        [InlineKeyboardButton("📋 المحرر النصي", callback_data="method_editor")],
    ])

def asset_choice_keyboard(recent_assets: List[str]) -> InlineKeyboardMarkup:
    # Uses simple strings as callback data, handled by conversation handler state logic
    buttons = [InlineKeyboardButton(asset, callback_data=f"asset_{asset}") for asset in recent_assets]
    # Ensure layout is reasonable, e.g., max 3 buttons per row
    keyboard = [buttons[i: i + 3] for i in range(0, len(buttons), 3)]
    keyboard.append([InlineKeyboardButton("✍️ اكتب أصلاً جديدًا", callback_data="asset_new")])
    return InlineKeyboardMarkup(keyboard)

def side_market_keyboard(current_market: str = "Futures") -> InlineKeyboardMarkup:
    # Uses simple strings as callback data, handled by conversation handler state logic
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🟢 LONG / {current_market}", callback_data="side_LONG"), InlineKeyboardButton(f"🔴 SHORT / {current_market}", callback_data="side_SHORT")],
        [InlineKeyboardButton(f"🔄 تغيير السوق", callback_data="side_menu")],
    ])

def market_choice_keyboard() -> InlineKeyboardMarkup:
     # Uses simple strings as callback data, handled by conversation handler state logic
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 Futures", callback_data="market_Futures"), InlineKeyboardButton("💎 Spot", callback_data="market_Spot")],
        [InlineKeyboardButton("⬅️ عودة", callback_data="market_back")],
    ])

def order_type_keyboard() -> InlineKeyboardMarkup:
     # Uses simple strings as callback data, handled by conversation handler state logic
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Market", callback_data="type_MARKET")],
        [InlineKeyboardButton("🎯 Limit", callback_data="type_LIMIT")],
        [InlineKeyboardButton("🚨 Stop Market", callback_data="type_STOP_MARKET")],
    ])

def review_final_keyboard(review_token: str) -> InlineKeyboardMarkup:
    short_token = review_token[:12] # Use shortened token
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نشر الآن", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "publish", short_token))],
        [InlineKeyboardButton("📢 اختيار القنوات", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "choose_channels", short_token)), InlineKeyboardButton("📝 إضافة ملاحظات", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "add_notes", short_token))],
        [InlineKeyboardButton("❌ إلغاء", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "cancel", short_token))],
    ])

def build_channel_picker_keyboard(review_token: str, channels: Iterable[Any], selected_ids: Set[int], page: int = 1, per_page: int = 6) -> InlineKeyboardMarkup:
    """Builds the paginated channel picker keyboard."""
    try:
        ch_list = list(channels)
        total = len(ch_list)
        total_pages = max(1, math.ceil(total / per_page))
        page = max(1, min(page, total_pages)) # Clamp page number
        start_idx, end_idx = (page - 1) * per_page, page * per_page
        page_items = ch_list[start_idx:end_idx]
        rows = []
        short_token = review_token[:12] # Use shortened token

        for ch in page_items:
            tg_chat_id_raw = _get_attr(ch, 'telegram_channel_id', 0)
            try:
                 tg_chat_id = int(tg_chat_id_raw)
                 if tg_chat_id == 0: continue # Skip if ID is invalid
            except (ValueError, TypeError):
                 logger.warning(f"Skipping channel with invalid telegram_channel_id: {tg_chat_id_raw}")
                 continue

            label = _truncate_text(_get_attr(ch, 'title') or f"قناة {tg_chat_id}", 25)
            is_selected = tg_chat_id in selected_ids
            status = "✅" if is_selected else "☑️"
            callback_data = CallbackBuilder.create(CallbackNamespace.PUBLICATION, "toggle", short_token, tg_chat_id, page)
            rows.append([InlineKeyboardButton(f"{status} {label}", callback_data=callback_data)])

        # Pagination controls
        nav_buttons = []
        if page > 1: nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, "nav", short_token, page - 1)))
        if total_pages > 1: nav_buttons.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
        if page < total_pages: nav_buttons.append(InlineKeyboardButton("➡️", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, "nav", short_token, page + 1)))
        if nav_buttons: rows.append(nav_buttons)

        # Action buttons
        rows.append([
            InlineKeyboardButton("🚀 نشر المحدد", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.CONFIRM, short_token)),
            InlineKeyboardButton("⬅️ عودة", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.BACK, short_token))
        ])
        return InlineKeyboardMarkup(rows)
    except Exception as e:
        logger.error(f"Error building channel picker: {e}", exc_info=True)
        # Fallback keyboard in case of error
        return InlineKeyboardMarkup([[InlineKeyboardButton("❌ خطأ - عودة", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.BACK, review_token[:12]))]])

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/keyboards.py ---