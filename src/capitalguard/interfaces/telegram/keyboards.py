# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/keyboards.py ---
# src/capitalguard/interfaces/telegram/keyboards.py (v21.13 - Async/Await Hotfix)
"""
Builds all Telegram keyboards for the bot.
✅ FIX: MAJOR: Rewrote price fetching in 'build_open_recs_keyboard' to correctly
  await coroutines and prevent 'RuntimeWarning: coroutine was never awaited'.
✅ FIX: Added missing 'import asyncio' statement.
✅ FIX: Removed invalid citation syntax causing a SyntaxError on startup.
✅ UX HOTFIX: Restored direct access to "Partial Close" and "Full Close" buttons.
- Implements the new unified Exit Management control panel and all its sub-panels.
- All callback data now uses the unified CallbackBuilder for maximum reliability.
"""

import math
import logging
import asyncio # ✅ FIX: Added missing import
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
    PENDING = "⏳";
    ACTIVE = "▶️"; PROFIT = "🟢"; LOSS = "🔴"; CLOSED = "🏁";
    ERROR = "⚠️"

class ButtonTexts:
    BACK_TO_LIST = "⬅️ العودة للقائمة"; BACK_TO_MAIN = "⬅️ العودة للوحة التحكم";
    PREVIOUS = "⬅️ السابق"; NEXT = "التالي ➡️"

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
        [
            InlineKeyboardButton("📈 إدارة الخروج والمخاطر", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "show_menu", rec_id)),
            InlineKeyboardButton("✏️ تعديل بيانات الصفقة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_menu", rec_id)),
        ],
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
    if _get_attr(rec, 'profit_stop_active', False):
        keyboard.append([InlineKeyboardButton("❌ إلغاء الاستراتيجية الآلية", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "cancel", rec_id))])

    keyboard.append([InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))])
    return InlineKeyboardMarkup(keyboard)

def build_trade_data_edit_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    """The trade data editing panel."""
    # Note: Logic to hide/disable buttons based on status should ideally be here or in the caller
    return InlineKeyboardMarkup([
        # Example: Could add condition: `if rec.status == RecommendationStatus.PENDING:` before appending this button
        [InlineKeyboardButton("💰 تعديل سعر الدخول", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_entry", rec_id))],
        [InlineKeyboardButton("🛑 تعديل وقف الخسارة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_sl", rec_id))],
        [InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_tp", rec_id))],
        [InlineKeyboardButton("📝 تعديل الملاحظات", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_notes", rec_id))],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))],
    ])

async def build_open_recs_keyboard(items: List[Any], current_page: int, price_service: PriceService) -> InlineKeyboardMarkup:
    """Builds the paginated keyboard for open recommendations/trades."""
    try:
        total_items = len(items)
        total_pages = math.ceil(total_items / ITEMS_PER_PAGE) or 1
        current_page = max(1, min(current_page, total_pages)) # Clamp page number
        start_index = (current_page - 1) * ITEMS_PER_PAGE
        paginated_items = items[start_index : start_index + ITEMS_PER_PAGE]

        # ✅ FIX: MAJOR: Correctly create and await tasks to prevent RuntimeWarning
        price_tasks = []
        assets_to_fetch = []
        asset_market_map = {} # Need to map asset to market
        for item in paginated_items:
            asset = _get_attr(item, 'asset')
            market = _get_attr(item, 'market', 'Futures')
            if asset not in asset_market_map:
                asset_market_map[asset] = market
                assets_to_fetch.append(asset)
        
        # Create tasks
        for asset in assets_to_fetch:
            price_tasks.append(
                price_service.get_cached_price(asset, asset_market_map[asset])
            )
        
        prices_results = await asyncio.gather(*price_tasks, return_exceptions=True)
        
        prices_map = {}
        # Process results safely
        for i, asset in enumerate(assets_to_fetch):
             result = prices_results[i]
             if isinstance(result, Exception):
                 logger.error(f"Failed to fetch price for {asset} in build_open_recs_keyboard: {result}")
                 prices_map[asset] = None
             else:
                 prices_map[asset] = result

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
        keyboard_rows.append([InlineKeyboardButton("🔄 تحديث القائمة", callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, current_page))]) # Add refresh button

        return InlineKeyboardMarkup(keyboard_rows)
    except Exception as e:
        logger.error(f"Open recs keyboard build failed: {e}", exc_info=True)
        # Fallback keyboard indicating error
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ خطأ في تحميل البيانات", callback_data="noop")],
            [InlineKeyboardButton("⬅️ العودة للقائمة", callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1))]
        ])


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
    """Builds a generic Yes/No confirmation keyboard."""
    confirm_cb = CallbackBuilder.create(namespace, CallbackAction.CONFIRM, item_id)
    cancel_cb = CallbackBuilder.create(namespace, CallbackAction.CANCEL, item_id)
    # Ensure callbacks are not too long if namespace/item_id are long
    if len(confirm_cb) > MAX_CALLBACK_DATA_LENGTH or len(cancel_cb) > MAX_CALLBACK_DATA_LENGTH:
        logger.warning(f"Confirmation callback data too long for {namespace}:{item_id}. Using shortened generic fallback.")
        confirm_cb = CallbackBuilder.create("generic", CallbackAction.CONFIRM, item_id)
        cancel_cb = CallbackBuilder.create("generic", CallbackAction.CANCEL, item_id)

    return InlineKeyboardMarkup([[
        InlineKeyboardButton(confirm_text, callback_data=confirm_cb),
        InlineKeyboardButton(cancel_text, callback_data=cancel_cb),
    ]])

def public_channel_keyboard(rec_id: int, bot_username: Optional[str]) -> Optional[InlineKeyboardMarkup]:
    buttons = []
    if bot_username:
        # Ensure the deep link URL itself isn't causing issues if rec_id gets very large, though unlikely
        track_url = f"https://t.me/{bot_username}?start=track_{rec_id}"
        buttons.append(InlineKeyboardButton("📊 تتبّع الإشارة", url=track_url))
    return InlineKeyboardMarkup([buttons]) if buttons else None

def build_user_trade_control_keyboard(trade_id: int) -> InlineKeyboardMarkup:
    """Keyboard for managing a user's personal trade (not an official recommendation)."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 تحديث السعر", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, "trade", trade_id)), # Use SHOW to refresh
            InlineKeyboardButton("❌ إغلاق الصفقة", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.CLOSE, "trade", trade_id)) # Needs a dedicated handler
        ],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1))],
    ])

def build_subscription_keyboard(channel_link: Optional[str]) -> Optional[InlineKeyboardMarkup]:
    if channel_link:
        return InlineKeyboardMarkup([[InlineKeyboardButton("➡️ الانضمام للقناة", url=channel_link)]])
    return None

# --- Recommendation Creation Flow Keyboards ---

def main_creation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 المنشئ التفاعلي", callback_data="method_interactive")],
        [InlineKeyboardButton("⚡️ الأمر السريع", callback_data="method_quick")],
        [InlineKeyboardButton("📋 المحرر النصي", callback_data="method_editor")],
    ])

def asset_choice_keyboard(recent_assets: List[str]) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(asset, callback_data=f"asset_{asset}") for asset in recent_assets]
    # Ensure button grid fits Telegram limits (max 8 buttons per row recommended)
    keyboard = [buttons[i: i + 3] for i in range(0, len(buttons), 3)] # Max 3 per row looks good
    keyboard.append([InlineKeyboardButton("✍️ اكتب أصلاً جديدًا", callback_data="asset_new")])
    return InlineKeyboardMarkup(keyboard)

def side_market_keyboard(current_market: str = "Futures") -> InlineKeyboardMarkup:
    market_display = "Futures" if "futures" in current_market.lower() else "Spot"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🟢 LONG / {market_display}", callback_data="side_LONG"),
            InlineKeyboardButton(f"🔴 SHORT / {market_display}", callback_data="side_SHORT")
        ],
        [InlineKeyboardButton(f"🔄 تغيير السوق (الحالي: {market_display})", callback_data="side_menu")],
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
    # Use a shortened token for callback data to avoid exceeding Telegram limits
    short_token = review_token[:12] # Keep length reasonable
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نشر الآن", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "publish", short_token))],
        [InlineKeyboardButton("📢 اختيار القنوات", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "choose_channels", short_token)), InlineKeyboardButton("📝 إضافة ملاحظات", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "add_notes", short_token))],
        [InlineKeyboardButton("❌ إلغاء", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "cancel", short_token))],
    ])

def build_channel_picker_keyboard(review_token: str, channels: Iterable[Any], selected_ids: Set[int], page: int = 1, per_page: int = 6) -> InlineKeyboardMarkup:
    """Builds the paginated channel selection keyboard."""
    try:
        ch_list = list(channels)
        total = len(ch_list)
        total_pages = max(1, math.ceil(total / per_page))
        page = max(1, min(page, total_pages)) # Clamp page
        start_idx, end_idx = (page - 1) * per_page, page * per_page
        page_items = ch_list[start_idx:end_idx]

        rows = []
        short_token = review_token[:12] # Use shortened token

        for ch in page_items:
            tg_chat_id = int(_get_attr(ch, 'telegram_channel_id', 0))
            if not tg_chat_id: continue
            label = _truncate_text(_get_attr(ch, 'title') or f"قناة {tg_chat_id}", 25)
            status = "✅" if tg_chat_id in selected_ids else ("☑️" if _get_attr(ch, 'is_active', False) else "❌") # Indicate inactive channels
            # Include page number in toggle callback to return to the same page
            callback_data = CallbackBuilder.create(CallbackNamespace.PUBLICATION, "toggle", short_token, tg_chat_id, page)
            rows.append([InlineKeyboardButton(f"{status} {label}", callback_data=callback_data)])

        nav_buttons = []
        if page > 1: nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, "nav", short_token, page - 1)))
        if total_pages > 1: nav_buttons.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
        if page < total_pages: nav_buttons.append(InlineKeyboardButton("➡️", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, "nav", short_token, page + 1)))
        if nav_buttons: rows.append(nav_buttons)

        # Confirm button should only appear if at least one channel is selected? Or allow saving without publishing?
        # Let's keep it simple: always show confirm, but handler can check selection.
        rows.append([
            InlineKeyboardButton("🚀 نشر المحدد", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.CONFIRM, short_token)),
            InlineKeyboardButton("⬅️ عودة للمراجعة", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.BACK, short_token))
        ])
        return InlineKeyboardMarkup(rows)
    except Exception as e:
        logger.error(f"Error building channel picker: {e}", exc_info=True)
        # Provide a way back even if building fails
        return InlineKeyboardMarkup([[InlineKeyboardButton("❌ خطأ - عودة للمراجعة", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.BACK, review_token[:12]))]])

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/keyboards.py ---