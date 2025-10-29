# --- src/capitalguard/interfaces/telegram/keyboards.py ---
# src/capitalguard/interfaces/telegram/keyboards.py (v21.15 - Decoupled)
"""
Builds all Telegram keyboards for the bot.
✅ HOTFIX: Decoupled from core notifier.
- Removed `public_channel_keyboard` (now lives in infrastructure/notify/telegram.py).
- Kept all other keyboards needed for interactive handlers (management, conversations).
"""

import math
import logging
import asyncio
from decimal import Decimal
from typing import List, Iterable, Set, Optional, Any, Dict, Tuple, Union
from enum import Enum

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from capitalguard.domain.entities import Recommendation, RecommendationStatus, ExitStrategy
from capitalguard.application.services.price_service import PriceService
# ❌ REMOVED: from capitalguard.interfaces.telegram.ui_texts import _pct
# (Helper functions like _pct, _format_price are now duplicated or imported from parsers)

# ✅ NEW: Import helpers from a safe, non-circular location (parsers)
from capitalguard.interfaces.telegram.parsers import parse_number

logger = logging.getLogger(__name__)

# ==================== CONSTANTS & CONFIGURATION ====================
ITEMS_PER_PAGE = 8
MAX_BUTTON_TEXT_LENGTH = 40
MAX_CALLBACK_DATA_LENGTH = 64
CALLBACK_DATA_VERSION = "2.1" # Use a version string

# ==================== CORE ARCHITECTURE ====================

class CallbackNamespace(Enum):
    """مساحات الأسماء المنطقية لبيانات الاستدعاء"""
    POSITION = "pos"
    RECOMMENDATION = "rec"
    EXIT_STRATEGY = "exit"
    NAVIGATION = "nav"
    PUBLICATION = "pub"
    FORWARD_PARSE = "fwd_parse" # Namespace for parsing review actions
    SAVE_TEMPLATE = "save_template" # Namespace for template saving confirmation
    MGMT = "mgmt" # Generic management actions like cancel input/all
    SYSTEM = "sys" # For general bot commands like /help, /settings

class CallbackAction(Enum):
    """الإجراءات القياسية المعرفة مسبقاً"""
    SHOW = "sh"
    UPDATE = "up"
    NAVIGATE = "nv"
    BACK = "bk"
    CLOSE = "cl"
    PARTIAL = "pt"
    CONFIRM = "cf"
    CANCEL = "cn"
    EDIT_FIELD = "edit_field" # Action for editing a specific field
    TOGGLE = "toggle" # For channel picker

class CallbackBuilder:
    """منشئ بيانات الاستدعاء المركزي"""
    
    @staticmethod
    def create(namespace: Union[CallbackNamespace, str], action: Union[CallbackAction, str], *params) -> str:
        """Builds a callback data string, ensuring it fits Telegram limits."""
        ns_val = namespace.value if isinstance(namespace, CallbackNamespace) else namespace
        act_val = action.value if isinstance(action, CallbackAction) else action
        param_str = ":".join(map(str, params))
        base = f"{ns_val}:{act_val}"
        if param_str: base = f"{base}:{param_str}"

        if len(base.encode('utf-8')) > MAX_CALLBACK_DATA_LENGTH:
            logger.warning(f"Callback data longer than {MAX_CALLBACK_DATA_LENGTH} bytes, truncating: {base}")
            base = base[:MAX_CALLBACK_DATA_LENGTH]
        return base

    @staticmethod
    def parse(callback_data: str) -> Dict[str, Any]:
        """Parses a callback data string."""
        try:
            parts = callback_data.split(':')
            return {
                'raw': callback_data,
                'namespace': parts[0] if parts else None,
                'action': parts[1] if len(parts) > 1 else None,
                'params': parts[2:] if len(parts) > 2 else []
            }
        except Exception as e:
            logger.error(f"Failed to parse callback data: {callback_data}, error: {e}")
            return {'raw': callback_data, 'error': 'Parsing failed'}

def parse_cq_parts(callback_data: str) -> List[str]:
    """Legacy helper, use CallbackBuilder.parse instead for structured data."""
    parsed = CallbackBuilder.parse(callback_data)
    parts = []
    if parsed.get('namespace'): parts.append(parsed['namespace'])
    if parsed.get('action'): parts.append(parsed['action'])
    if parsed.get('params'): parts.extend(parsed['params'])
    return parts

# ==================== DOMAIN MODELS ====================

class StatusIcons:
    """رموز الحالات المختلفة للتوصيات والصفقات"""
    PENDING = "⏳"; ACTIVE = "▶️"; PROFIT = "🟢"; LOSS = "🔴"; CLOSED = "🏁"; ERROR = "⚠️"
    BREAK_EVEN = "🛡️"; SHADOW = "👻";

class ButtonTexts:
    """نصوص الأزرار القياسية"""
    BACK = "⬅️ عودة"
    BACK_TO_LIST = "⬅️ العودة للقائمة"; BACK_TO_MAIN = "⬅️ العودة للوحة التحكم";
    PREVIOUS = "⬅️ السابق"; NEXT = "التالي ➡️"; CONFIRM = "✅ تأكيد"; CANCEL = "❌ إلغاء";
    EDIT = "✏️ تعديل"; UPDATE = "🔄 تحديث"; CLOSE = "❌ إغلاق";

# ==================== CORE UTILITIES ====================

# Local helpers, decoupled from ui_texts
def _to_decimal(value: Any, default: Decimal = Decimal('0')) -> Decimal:
    if isinstance(value, Decimal): return value if value.is_finite() else default
    if value is None: return default
    try: d = Decimal(str(value)); return d if d.is_finite() else default
    except (InvalidOperation, TypeError, ValueError): return default

def _format_price(price: Any) -> str:
    price_dec = _to_decimal(price); return "N/A" if not price_dec.is_finite() else f"{price_dec:g}"

def _pct(entry: Any, target_price: Any, side: str) -> float:
    try:
        entry_dec = _to_decimal(entry); target_dec = _to_decimal(target_price);
        if not entry_dec.is_finite() or entry_dec.is_zero() or not target_dec.is_finite(): return 0.0
        side_upper = (str(side.value) if hasattr(side, 'value') else str(side) or "").upper()
        if side_upper == "LONG": pnl = ((target_dec / entry_dec) - 1) * 100
        elif side_upper == "SHORT": pnl = ((entry_dec / target_dec) - 1) * 100
        else: return 0.0
        return float(pnl)
    except (InvalidOperation, TypeError, ZeroDivisionError): return 0.0

def _get_attr(obj: Any, attr: str, default: Any = None) -> Any:
    val = getattr(obj, attr, default)
    return val.value if hasattr(val, 'value') else val

def _truncate_text(text: str, max_length: int = MAX_BUTTON_TEXT_LENGTH) -> str:
    text = str(text or "")
    return text if len(text) <= max_length else text[:max_length-3] + "..."


# ==================== BUSINESS LOGIC LAYER ====================

class StatusDeterminer:
    """محلل الحالات المتقدم"""
    
    @staticmethod
    def determine_icon(item: Any, live_price: Optional[float] = None) -> str:
        """تحديد الرمز المناسب لحالة العنصر"""
        try:
            status = _get_attr(item, 'status'); status_value = status.value if hasattr(status, 'value') else status
            
            if status_value in [RecommendationStatus.PENDING.value, 'PENDING']: return StatusIcons.PENDING
            if status_value in [RecommendationStatus.CLOSED.value, 'CLOSED']: return StatusIcons.CLOSED
            
            if status_value in [RecommendationStatus.ACTIVE.value, 'ACTIVE', 'OPEN']:
                entry_dec = _to_decimal(_get_attr(item, 'entry'))
                sl_dec = _to_decimal(_get_attr(item, 'stop_loss'))
                if entry_dec > 0 and sl_dec > 0 and abs(entry_dec - sl_dec) / entry_dec < Decimal('0.0005'): # 0.05%
                    return StatusIcons.BREAK_EVEN
                if live_price is not None:
                    side = _get_attr(item, 'side');
                    if entry_dec > 0: pnl = _pct(entry_dec, live_price, side); return StatusIcons.PROFIT if pnl >= 0 else StatusIcons.LOSS
                return StatusIcons.ACTIVE
            return StatusIcons.ERROR
        except Exception: return StatusIcons.ERROR

class NavigationBuilder:
    """منشئ أنظمة التنقل"""
    
    @staticmethod
    def build_pagination(
        current_page: int, 
        total_pages: int, 
        base_ns: CallbackNamespace = CallbackNamespace.NAVIGATION,
        base_action: CallbackAction = CallbackAction.NAVIGATE,
        extra_params: Tuple = ()
    ) -> List[List[InlineKeyboardButton]]:
        """بناء أزرار الترقيم"""
        buttons = []
        if current_page > 1: buttons.append(InlineKeyboardButton(ButtonTexts.PREVIOUS, callback_data=CallbackBuilder.create(base_ns, base_action, current_page - 1, *extra_params)))
        if total_pages > 1: buttons.append(InlineKeyboardButton(f"📄 {current_page}/{total_pages}", callback_data="noop"))
        if current_page < total_pages: buttons.append(InlineKeyboardButton(ButtonTexts.NEXT, callback_data=CallbackBuilder.create(base_ns, base_action, current_page + 1, *extra_params)))
        return [buttons] if buttons else []

# ==================== KEYBOARD FACTORIES ====================

async def build_open_recs_keyboard(items: List[Any], current_page: int, price_service: PriceService) -> InlineKeyboardMarkup:
    """Builds the paginated keyboard for open recommendations/trades."""
    try:
        total_items = len(items); total_pages = math.ceil(total_items / ITEMS_PER_PAGE) or 1; current_page = max(1, min(current_page, total_pages)); start_index = (current_page - 1) * ITEMS_PER_PAGE; paginated_items = items[start_index : start_index + ITEMS_PER_PAGE];
        
        assets_to_fetch = {(_get_attr(item, 'asset'), _get_attr(item, 'market', 'Futures')) for item in paginated_items if _get_attr(item, 'asset')}
        price_tasks = [price_service.get_cached_price(asset, market) for asset, market in assets_to_fetch]
        price_results = await asyncio.gather(*price_tasks, return_exceptions=True);
        prices_map = {asset_market[0]: price for asset_market, price in zip(assets_to_fetch, price_results) if not isinstance(price, Exception)}

        keyboard_rows = []
        for item in paginated_items:
            rec_id, asset, side = _get_attr(item, 'id'), _get_attr(item, 'asset'), _get_attr(item, 'side'); live_price = prices_map.get(asset); status_icon = StatusDeterminer.determine_icon(item, live_price); button_text = f"#{rec_id} - {asset} ({side})";
            if live_price is not None and status_icon in [StatusIcons.PROFIT, StatusIcons.LOSS]: pnl = _pct(_get_attr(item, 'entry'), live_price, side); button_text = f"{status_icon} {button_text} | PnL: {pnl:+.2f}%";
            else: button_text = f"{status_icon} {button_text}";
            
            item_type = 'trade' if getattr(item, 'is_user_trade', False) else 'rec';
            callback_data = CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, item_type, rec_id);
            keyboard_rows.append([InlineKeyboardButton(_truncate_text(button_text), callback_data=callback_data)]);
        
        keyboard_rows.extend(NavigationBuilder.build_pagination(current_page, total_pages));
        keyboard_rows.append([InlineKeyboardButton("🔄 تحديث القائمة", callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, current_page))]);
        return InlineKeyboardMarkup(keyboard_rows)
    except Exception as e:
        logger.error(f"Open recs keyboard build failed: {e}", exc_info=True);
        return InlineKeyboardMarkup([[InlineKeyboardButton("⚠️ Error Loading Data", callback_data="noop")],[InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1))]])

def build_editable_review_card(parsed_data: Dict[str, Any]) -> InlineKeyboardMarkup:
    """Builds the interactive review card with edit buttons for parsed data."""
    asset = parsed_data.get('asset', 'N/A')
    side = parsed_data.get('side', 'N/A')
    entry = _format_price(parsed_data.get('entry'))
    stop_loss = _format_price(parsed_data.get('stop_loss'))
    targets = parsed_data.get('targets', [])

    target_items = []
    for t in targets:
        price_str = _format_price(t.get('price'))
        close_pct = t.get('close_percent', 0.0)
        item_str = price_str
        if close_pct > 0:
             item_str += f"@{int(close_pct) if float(close_pct).is_integer() else close_pct:.1f}%"
        target_items.append(item_str)
    target_str = ", ".join(target_items)

    ns = CallbackNamespace.FORWARD_PARSE
    keyboard = [
        [
            InlineKeyboardButton(f"📝 {_truncate_text('Asset: '+asset)}", callback_data=CallbackBuilder.create(ns, CallbackAction.EDIT_FIELD, "asset")),
            InlineKeyboardButton(f"📝 {_truncate_text('Side: '+side)}", callback_data=CallbackBuilder.create(ns, CallbackAction.EDIT_FIELD, "side")),
        ],
        [
            InlineKeyboardButton(f"📝 {_truncate_text('Entry: '+entry)}", callback_data=CallbackBuilder.create(ns, CallbackAction.EDIT_FIELD, "entry")),
            InlineKeyboardButton(f"📝 {_truncate_text('SL: '+stop_loss)}", callback_data=CallbackBuilder.create(ns, CallbackAction.EDIT_FIELD, "stop_loss")),
        ],
        [ InlineKeyboardButton(f"📝 {_truncate_text('Targets: '+target_str, 50)}", callback_data=CallbackBuilder.create(ns, CallbackAction.EDIT_FIELD, "targets")) ],
        [
            InlineKeyboardButton(ButtonTexts.CONFIRM + " & Track", callback_data=CallbackBuilder.create(ns, CallbackAction.CONFIRM, "save")),
            InlineKeyboardButton(ButtonTexts.CANCEL, callback_data=CallbackBuilder.create(ns, CallbackAction.CANCEL, "discard")),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def analyst_control_panel_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    """Unified control panel for active recommendations."""
    rec_id = _get_attr(rec, 'id')
    status = _get_attr(rec, 'status') # Should be RecommendationStatus enum member
    ns_rec = CallbackNamespace.RECOMMENDATION
    ns_pos = CallbackNamespace.POSITION
    ns_exit = CallbackNamespace.EXIT_STRATEGY
    ns_nav = CallbackNamespace.NAVIGATION

    if status != RecommendationStatus.ACTIVE:
         return InlineKeyboardMarkup([[
              InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(ns_nav, CallbackAction.NAVIGATE, 1))
         ]])

    keyboard = [
        [
            InlineKeyboardButton("🔄 تحديث السعر", callback_data=CallbackBuilder.create(ns_pos, CallbackAction.SHOW, 'rec', rec_id)),
            InlineKeyboardButton("💰 إغلاق جزئي", callback_data=CallbackBuilder.create(ns_rec, "partial_close_menu", rec_id)),
            InlineKeyboardButton("❌ إغلاق كلي", callback_data=CallbackBuilder.create(ns_rec, "close_menu", rec_id)),
        ],
        [
            InlineKeyboardButton("📈 إدارة الخروج", callback_data=CallbackBuilder.create(ns_exit, "show_menu", rec_id)),
            InlineKeyboardButton("✏️ تعديل البيانات", callback_data=CallbackBuilder.create(ns_rec, "edit_menu", rec_id)),
        ],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(ns_nav, CallbackAction.NAVIGATE, 1))],
    ]
    return InlineKeyboardMarkup(keyboard)

def build_user_trade_control_keyboard(trade_id: int) -> InlineKeyboardMarkup:
    """Keyboard for managing a personal UserTrade."""
    ns_pos = CallbackNamespace.POSITION
    ns_nav = CallbackNamespace.NAVIGATION
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 تحديث السعر", callback_data=CallbackBuilder.create(ns_pos, CallbackAction.SHOW, "trade", trade_id)),
            InlineKeyboardButton("❌ إغلاق الصفقة", callback_data=CallbackBuilder.create(ns_pos, CallbackAction.CLOSE, "trade", trade_id))
        ],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(ns_nav, CallbackAction.NAVIGATE, 1))],
    ])

def build_confirmation_keyboard(
    namespace: Union[CallbackNamespace, str],
    item_id: Union[int, str],
    confirm_text: str = ButtonTexts.CONFIRM,
    cancel_text: str = ButtonTexts.CANCEL
) -> InlineKeyboardMarkup:
    """Builds a generic Yes/No confirmation keyboard using CallbackBuilder."""
    confirm_cb = CallbackBuilder.create(namespace, CallbackAction.CONFIRM, item_id)
    cancel_cb = CallbackBuilder.create(namespace, CallbackAction.CANCEL, item_id)
    if len(confirm_cb.encode('utf-8')) > MAX_CALLBACK_DATA_LENGTH or len(cancel_cb.encode('utf-8')) > MAX_CALLBACK_DATA_LENGTH:
         logger.warning(f"Confirm CB data > 64 bytes for {namespace}:{item_id}.")
         # Fallback to a shorter, generic callback if needed, or shorten item_id
    return InlineKeyboardMarkup([[ InlineKeyboardButton(confirm_text, callback_data=confirm_cb), InlineKeyboardButton(cancel_text, callback_data=cancel_cb), ]])

# --- Recommendation Creation Flow Keyboards ---
def main_creation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 المنشئ التفاعلي", callback_data="method_interactive")],
        [InlineKeyboardButton("⚡️ الأمر السريع", callback_data="method_quick")],
        [InlineKeyboardButton("📋 المحرر النصي", callback_data="method_editor")],
    ])

def asset_choice_keyboard(recent_assets: List[str]) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(asset, callback_data=f"asset_{asset}") for asset in recent_assets]
    keyboard = [buttons[i: i + 3] for i in range(0, len(buttons), 3)] # Max 3 per row
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
    """Final review keyboard using CallbackBuilder."""
    short_token = review_token[:12]
    ns = CallbackNamespace.RECOMMENDATION
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نشر الآن", callback_data=CallbackBuilder.create(ns, "publish", short_token))],
        [
            InlineKeyboardButton("📢 اختيار القنوات", callback_data=CallbackBuilder.create(ns, "choose_channels", short_token)),
            InlineKeyboardButton("📝 إضافة ملاحظات", callback_data=CallbackBuilder.create(ns, "add_notes", short_token))
        ],
        [InlineKeyboardButton("❌ إلغاء", callback_data=CallbackBuilder.create(ns, "cancel", short_token))],
    ])

def build_channel_picker_keyboard(review_token: str, channels: Iterable[Any], selected_ids: Set[int], page: int = 1, per_page: int = 6) -> InlineKeyboardMarkup:
    """Builds the paginated channel selection keyboard using CallbackBuilder."""
    try:
        ch_list = list(channels); total = len(ch_list); total_pages = max(1, math.ceil(total / per_page)); page = max(1, min(page, total_pages)); start_idx, end_idx = (page - 1) * per_page, page * per_page; page_items = ch_list[start_idx:end_idx];
        rows = []; short_token = review_token[:12]; ns = CallbackNamespace.PUBLICATION;
        for ch in page_items:
            tg_chat_id = int(_get_attr(ch, 'telegram_channel_id', 0));
            if not tg_chat_id: continue;
            label = _truncate_text(_get_attr(ch, 'title') or f"Channel {tg_chat_id}", 25); status = "✅" if tg_chat_id in selected_ids else ("☑️" if _get_attr(ch, 'is_active', False) else "❌"); callback_data = CallbackBuilder.create(ns, CallbackAction.TOGGLE, short_token, tg_chat_id, page); rows.append([InlineKeyboardButton(f"{status} {label}", callback_data=callback_data)]);
        nav_buttons = [];
        if page > 1: nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=CallbackBuilder.create(ns, "nav", short_token, page - 1)))
        if total_pages > 1: nav_buttons.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
        if page < total_pages: nav_buttons.append(InlineKeyboardButton("➡️", callback_data=CallbackBuilder.create(ns, "nav", short_token, page + 1)))
        if nav_buttons: rows.append(nav_buttons);
        rows.append([ InlineKeyboardButton("🚀 نشر المحدد", callback_data=CallbackBuilder.create(ns, CallbackAction.CONFIRM, short_token)), InlineKeyboardButton("⬅️ عودة للمراجعة", callback_data=CallbackBuilder.create(ns, CallbackAction.BACK, short_token)) ]);
        return InlineKeyboardMarkup(rows)
    except Exception as e:
        logger.error(f"Error building channel picker: {e}", exc_info=True);
        return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Error - Back to Review", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.BACK, review_token[:12]))]])

# ❌ REMOVED: public_channel_keyboard (moved to infrastructure/notify/telegram.py)

def build_subscription_keyboard(channel_link: Optional[str]) -> Optional[InlineKeyboardMarkup]:
     if channel_link: return InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Join Channel", url=channel_link)]])
     return None

# --- Other keyboards (e.g., analyst submenus) ---

def build_close_options_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    ns = CallbackNamespace.RECOMMENDATION
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📉 إغلاق بسعر السوق", callback_data=CallbackBuilder.create(ns, "close_market", rec_id))],
        [InlineKeyboardButton("✍️ إغلاق بسعر محدد", callback_data=CallbackBuilder.create(ns, "close_manual", rec_id))],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))],
    ])

def build_trade_data_edit_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    ns = CallbackNamespace.RECOMMENDATION
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 تعديل سعر الدخول", callback_data=CallbackBuilder.create(ns, "edit_entry", rec_id))], # Handler must check status
        [InlineKeyboardButton("🛑 تعديل وقف الخسارة", callback_data=CallbackBuilder.create(ns, "edit_sl", rec_id))],
        [InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=CallbackBuilder.create(ns, "edit_tp", rec_id))],
        [InlineKeyboardButton("📝 تعديل الملاحظات", callback_data=CallbackBuilder.create(ns, "edit_notes", rec_id))],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))],
    ])

def build_exit_management_keyboard(rec: RecommendationEntity) -> InlineKeyboardMarkup:
    """Builds the exit strategy management panel using CallbackBuilder."""
    rec_id = _get_attr(rec, 'id')
    is_strategy_active = _get_attr(rec, 'profit_stop_active', False) # Assumes entity has this attr
    ns = CallbackNamespace.EXIT_STRATEGY

    keyboard = [
        [InlineKeyboardButton("⚖️ نقل الوقف إلى التعادل", callback_data=CallbackBuilder.create(ns, "move_to_be", rec_id))],
        [InlineKeyboardButton("🔒 تفعيل حجز ربح ثابت", callback_data=CallbackBuilder.create(ns, "set_fixed", rec_id))],
        [InlineKeyboardButton("📈 تفعيل الوقف المتحرك", callback_data=CallbackBuilder.create(ns, "set_trailing", rec_id))],
    ]
    if is_strategy_active:
        keyboard.append([InlineKeyboardButton("❌ إلغاء الاستراتيجية الآلية", callback_data=CallbackBuilder.create(ns, "cancel", rec_id))])

    keyboard.append([InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))])
    return InlineKeyboardMarkup(keyboard)

def build_partial_close_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    """Builds the partial close keyboard using CallbackBuilder."""
    ns = CallbackNamespace.RECOMMENDATION
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 إغلاق 25%", callback_data=CallbackBuilder.create(ns, CallbackAction.PARTIAL, rec_id, "25"))],
        [InlineKeyboardButton("💰 إغلاق 50%", callback_data=CallbackBuilder.create(ns, CallbackAction.PARTIAL, rec_id, "50"))],
        [InlineKeyboardButton("✍️ نسبة مخصصة", callback_data=CallbackBuilder.create(ns, "partial_close_custom", rec_id))],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))],
    ])

# ==================== EXPORTS ====================
# (List all functions that handlers need to import)
__all__ = [
    'build_open_recs_keyboard', 'main_creation_keyboard', 'analyst_control_panel_keyboard',
    'build_user_trade_control_keyboard', 'build_close_options_keyboard',
    'build_trade_data_edit_keyboard', 'build_exit_management_keyboard',
    'build_partial_close_keyboard', 'build_confirmation_keyboard',
    'asset_choice_keyboard', 'side_market_keyboard', 'market_choice_keyboard',
    'order_type_keyboard', 'review_final_keyboard', 'build_channel_picker_keyboard',
    'build_subscription_keyboard', 'build_editable_review_card',
    'CallbackBuilder', 'StatusDeterminer', 'NavigationBuilder',
    'StatusIcons', 'ButtonTexts', 'CallbackNamespace', 'CallbackAction',
    'parse_cq_parts',
]
# --- END OF FILE ---