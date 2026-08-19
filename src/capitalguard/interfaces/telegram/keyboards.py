# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/keyboards.py ---
# File: src/capitalguard/interfaces/telegram/keyboards.py
# Version: v58.0.0-LIVE-CARD (Refresh Button Added)
# ✅ THE FIX: Added 'Refresh' button to public_channel_keyboard
# 🎯 IMPACT: Maintains ALL existing functionality while adding the new refresh feature

import math
import logging
import asyncio
from decimal import Decimal
from typing import List, Iterable, Set, Optional, Any, Dict, Tuple, Union
from enum import Enum

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from capitalguard.config import settings

from capitalguard.domain.entities import Recommendation as RecommendationEntity, RecommendationStatus
from capitalguard.domain.entities import UserTradeStatus

# ✅ R2: Import PriceService for type hinting
if False:
    from capitalguard.application.services.price_service import PriceService

logger = logging.getLogger(__name__)

# --- Constants ---
ITEMS_PER_PAGE_HUB = 6 # (Design 2 has 6 items)
ITEMS_PER_PAGE_CHANNELS = 8
MAX_BUTTON_TEXT_LENGTH = 60 # (Increased for card text)
MAX_CALLBACK_DATA_LENGTH = 64

# --- Internal Helpers ---
def _to_decimal(value: Any, default: Decimal = Decimal('0')) -> Decimal:
    if isinstance(value, Decimal): 
        return value if value.is_finite() else default
    if value is None: 
        return default
    try:
        d = Decimal(str(value))
        return d if d.is_finite() else default
    except Exception: 
        return default

def _format_price(price: Any) -> str:
    price_dec = _to_decimal(price)
    if not price_dec.is_finite() or price_dec == Decimal(0):
        return "N/A"
    return f"{price_dec:g}"

def _pct(entry: Any, target_price: Any, side: str) -> float:
    try:
        entry_dec = _to_decimal(entry)
        target_dec = _to_decimal(target_price)
        if not entry_dec.is_finite() or entry_dec.is_zero() or not target_dec.is_finite(): 
            return 0.0
        side_upper = (str(side) or "").upper()
        if side_upper == "LONG": 
            pnl = ((target_dec / entry_dec) - 1) * 100
        elif side_upper == "SHORT": 
            pnl = ((entry_dec / target_dec) - 1) * 100
        else: 
            return 0.0
        return float(pnl) 
    except Exception: 
        return 0.0

def _truncate_text(text: str, max_length: int = MAX_BUTTON_TEXT_LENGTH) -> str:
    """Truncates text with ellipsis if too long."""
    text = str(text or "")
    return text if len(text) <= max_length else text[:max_length-3] + "..."

def _get_attr(obj: Any, attr: str, default: Any = None) -> Any:
    """Safely gets attribute, handles domain objects with .value."""
    val = getattr(obj, attr, default)
    return getattr(val, 'value', val)


# --- Core Callback Architecture ---
class CallbackNamespace(Enum):
    POSITION = "pos"
    RECOMMENDATION = "rec"
    EXIT_STRATEGY = "exit"
    NAVIGATION = "nav" # (Legacy)
    PUBLICATION = "pub"
    FORWARD_PARSE = "fwd_parse"
    SAVE_TEMPLATE = "save_template"
    MGMT = "mgmt" # ✅ R2: New namespace for Hub navigation

class CallbackAction(Enum):
    SHOW = "sh"
    UPDATE = "up"
    NAVIGATE = "nv" # (Legacy)
    SHOW_LIST = "show_list" # ✅ R2: New navigation action
    HUB = "hub" # ✅ R2: New hub action
    BACK = "bk"
    CLOSE = "cl"
    PARTIAL = "pt"
    CONFIRM = "cf"
    WATCH_CHANNEL = "watch"
    CANCEL = "cn"
    EDIT_FIELD = "edit_field"
    TOGGLE = "toggle"
    ACTIVATE_TRADE = "activate_trade"

class CallbackBuilder:
    @staticmethod
    def create(namespace: Union[CallbackNamespace, str], action: Union[CallbackAction, str], *params) -> str:
        """Builds a callback data string, ensuring it fits Telegram limits."""
        ns_val = namespace.value if isinstance(namespace, CallbackNamespace) else namespace
        act_val = action.value if isinstance(action, CallbackAction) else action
        # ✅ R2: Ensure all params are strings
        param_str = ":".join(map(str, params))
        base = f"{ns_val}:{act_val}"
        if param_str: 
            base = f"{base}:{param_str}"

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
    """Legacy helper."""
    parsed = CallbackBuilder.parse(callback_data)
    parts = []
    if parsed.get('namespace'): 
        parts.append(parsed['namespace'])
    if parsed.get('action'): 
        parts.append(parsed['action'])
    if parsed.get('params'): 
        parts.extend(parsed['params'])
    return parts


# --- UI Constants ---
class StatusIcons:
    PENDING = "⏳" # Used for PENDING_ACTIVATION
    ACTIVE = "▶️"
    PROFIT = "🟢"
    LOSS = "🔴"
    CLOSED = "🏁"
    ERROR = "⚠️"
    BREAK_EVEN = "🔵"
    SHADOW = "👻"
    WATCHLIST = "👁️"

class ButtonTexts:
    BACK_TO_LIST = "⬅️ العودة للقائمة"
    BACK_TO_MAIN = "🏠 الرئيسية" # ✅ R2: "Home"
    PREVIOUS = "⬅️"
    NEXT = "➡️"
    CONFIRM = "✅ تأكيد"
    CANCEL = "❌ إلغاء"


# --- Status & Navigation Logic ---
class StatusDeterminer:
    @staticmethod
    def determine_icon(item: Any, live_price: Optional[float] = None) -> str:
        """Determines the status icon based on item state and live price."""
        try:
            is_trade = getattr(item, 'is_user_trade', False)
            if is_trade:
                status_value = _get_attr(item, 'orm_status_value')
            else:
                status = _get_attr(item, 'status')
                status_value = status.value if hasattr(status, 'value') else str(status).upper()
            
            if status_value == UserTradeStatus.WATCHLIST.value: 
                return StatusIcons.WATCHLIST
            if status_value == UserTradeStatus.PENDING_ACTIVATION.value: 
                return StatusIcons.PENDING
            if status_value == RecommendationStatus.PENDING.value: 
                return StatusIcons.PENDING
            
            if status_value == RecommendationStatus.CLOSED.value: 
                return StatusIcons.CLOSED
                
            if status_value in [RecommendationStatus.ACTIVE.value, UserTradeStatus.ACTIVATED.value]:
                entry_dec = _to_decimal(_get_attr(item, 'entry'))
                sl_dec = _to_decimal(_get_attr(item, 'stop_loss'))
                
                if live_price is not None:
                    side = _get_attr(item, 'side')
                    if entry_dec > 0:
                        pnl = _pct(entry_dec, live_price, side)
                        if pnl > 0.05:
                            return StatusIcons.PROFIT
                        elif pnl < -0.05:
                            return StatusIcons.LOSS
                        else:
                            return StatusIcons.BREAK_EVEN # PnL is near 0
                return StatusIcons.ACTIVE
        
        except Exception as e:
            logger.warning(f"Status determination failed: {e}")
        return StatusIcons.ERROR

class NavigationBuilder:
    @staticmethod
    def build_pagination(current_page: int, total_pages: int,
                         base_ns: CallbackNamespace = CallbackNamespace.MGMT,
                         base_action: CallbackAction = CallbackAction.SHOW_LIST,
                         extra_params: Tuple = ()
                         ) -> List[InlineKeyboardButton]:
        """Builds pagination buttons using CallbackBuilder."""
        buttons = []
        if current_page > 1: 
            buttons.append(InlineKeyboardButton(ButtonTexts.PREVIOUS, callback_data=CallbackBuilder.create(base_ns, base_action, *(extra_params + (current_page - 1,)))))
        if total_pages > 1: 
            buttons.append(InlineKeyboardButton(f"📄 {current_page}/{total_pages}", callback_data="noop"))
        if current_page < total_pages: 
            buttons.append(InlineKeyboardButton(ButtonTexts.NEXT, callback_data=CallbackBuilder.create(base_ns, base_action, *(extra_params + (current_page + 1,)))))
        
        return buttons

# --- Keyboard Factories (R2 REFACTORED) ---

async def build_open_recs_keyboard(
    items_list: List[Any], 
    current_page: int, 
    price_service: "PriceService",
    list_type: str # ✅ R2: "activated" or "watchlist"
) -> InlineKeyboardMarkup:
    """
    ✅ R2 (Design 2 & 4) Updated Keyboard Builder.
    Builds the paginated "Card UI" keyboard for Activated or Watchlist items.
    """
    try:
        if not items_list:
            return InlineKeyboardMarkup([
                [InlineKeyboardButton("📭 لا توجد صفقات هنا.", callback_data="noop")],
                [InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, CallbackAction.HUB))]
            ])

        # --- 2. Paginate the display list ---
        total_items = len(items_list)
        total_pages = math.ceil(total_items / ITEMS_PER_PAGE_HUB) or 1
        current_page = max(1, min(current_page, total_pages))
        start_index = (current_page - 1) * ITEMS_PER_PAGE_HUB
        paginated_items = items_list[start_index : start_index + ITEMS_PER_PAGE_HUB]
        
        # --- 3. Fetch prices only for items on the current page ---
        assets_to_fetch = {
            (_get_attr(item, 'asset'), _get_attr(item, 'market', 'Futures')) 
            for item in paginated_items if _get_attr(item, 'asset')
        }
        
        price_tasks = [price_service.get_cached_price(asset, market) for asset, market in assets_to_fetch]
        price_results = await asyncio.gather(*price_tasks, return_exceptions=True)
        prices_map = {asset_market[0]: price for asset_market, price in zip(assets_to_fetch, price_results) if not isinstance(price, Exception) and price is not None}

        # --- 4. Build keyboard rows (Design 2 & 4) ---
        keyboard_rows = []
        for item in paginated_items:
            rec_id, asset, side = _get_attr(item, 'id'), _get_attr(item, 'asset'), _get_attr(item, 'side')
            entry = _get_attr(item, 'entry')
            live_price = prices_map.get(asset)
            
            status_icon = StatusDeterminer.determine_icon(item, live_price)
            item_type_str = 'trade' if getattr(item, 'is_user_trade', False) else 'rec'

            # Build a compact, source-aware card with a stable ID.
            card_lines = []
            record_id = getattr(item, 'record_id', None) or rec_id
            display_ref = getattr(item, 'display_ref', None) or f'#{record_id}'
            public_ref = getattr(item, 'public_ref', None)
            identity_text = f'{display_ref} · {public_ref}' if public_ref and public_ref != display_ref else display_ref
            channel_codes = getattr(item, 'channel_codes', None) or []
            single_channel = getattr(item, 'channel_code', None)
            channel_text = ', '.join(channel_codes) if channel_codes else single_channel
            channel_line = f"القناة: {channel_text}" if channel_text else None
            source_type = getattr(item, 'source_type', '')
            if source_type == 'DIRECT_INPUT':
                source_label = '📝 LOG'
            elif source_type == 'TRACKED_RECOMMENDATION':
                source_label = '📡 TRACKED'
            else:
                source_label = '🧠 ANALYST'

            if list_type == "activated":
                pnl_str = "PnL: N/A"
                if live_price is not None:
                    pnl = _pct(entry, live_price, side)
                    pnl_str = f"PnL: {pnl:+.2f}%"
                
                card_lines = [
                    f"{status_icon} {source_label} • {asset} ({side})",
                    f"ID: {identity_text}",
                    *([channel_line] if channel_line else []),
                    pnl_str,
                    f"Entry: {_format_price(entry)}"
                ]
            elif list_type == "history":
                pnl = getattr(item, "final_pnl_percentage", None)
                exit_price = _get_attr(item, "exit_price")
                pnl_str = f"PnL: {float(pnl):+.2f}%" if pnl is not None else "PnL: N/A"
                card_lines = [
                    f"🏁 {source_label} • {asset} ({side})",
                    f"ID: {identity_text}",
                    *([channel_line] if channel_line else []),
                    pnl_str,
                    f"Entry: {_format_price(entry)} | Exit: {_format_price(exit_price)}",
                ]
            else: # Watchlist or source-separated lists.
                status_icon = StatusIcons.ACTIVE if getattr(item, 'unified_status', '') == 'ACTIVE' else StatusIcons.WATCHLIST
                price_str = f"السعر الحالي: {_format_price(live_price)}" if live_price else "السعر: N/A"
                card_lines = [
                    f"{status_icon} {source_label} • {asset} ({side})",
                    f"ID: {identity_text}",
                    *([channel_line] if channel_line else []),
                    price_str,
                    f"Entry: {_format_price(entry)}"
                ]

            card_text = "\n".join(card_lines)
            
            # Add separator
            keyboard_rows.append([InlineKeyboardButton("━━━━━━━━━━━━━━━━━━", callback_data="noop")])
            
            # The card itself is the button
            callback_data = CallbackBuilder.create(
                CallbackNamespace.POSITION, CallbackAction.SHOW, 
                item_type_str, rec_id, 
                list_type, current_page # Pass context for "Back" button
            )
            keyboard_rows.append([InlineKeyboardButton(_truncate_text(card_text, 60), callback_data=callback_data)])
        
        # --- 5. Add navigation ---
        keyboard_rows.append([InlineKeyboardButton("━━━━━━━━━━━━━━━━━━", callback_data="noop")])
        
        nav_buttons = NavigationBuilder.build_pagination(
            current_page, 
            total_pages,
            base_ns=CallbackNamespace.MGMT,
            base_action=CallbackAction.SHOW_LIST,
            extra_params=(list_type,) # Pass list_type in navigation
        )
        if nav_buttons:
            keyboard_rows.append(nav_buttons)
            
        keyboard_rows.append([InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, CallbackAction.HUB))])
        return InlineKeyboardMarkup(keyboard_rows)
    
    except Exception as e:
        logger.error(f"Open recs keyboard build failed: {e}", exc_info=True)
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ Error Loading Data", callback_data="noop")],
            [InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, CallbackAction.HUB))]
        ])

# ✅ R2 (Design 5): New keyboard for showing channel list
def build_channels_list_keyboard(
    channels_summary: List[Dict[str, Any]], 
    current_page: int, 
    list_type: str = "channels"
) -> InlineKeyboardMarkup:
    """
    Builds the paginated "Card UI" keyboard for Watched Channels list (Design 5).
    """
    try:
        if not channels_summary:
            return InlineKeyboardMarkup([
                [InlineKeyboardButton("📭 لا توجد قنوات للمتابعة.", callback_data="noop")],
                [InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, CallbackAction.HUB))]
            ])

        # --- Paginate ---
        total_items = len(channels_summary)
        total_pages = math.ceil(total_items / ITEMS_PER_PAGE_CHANNELS) or 1
        current_page = max(1, min(current_page, total_pages))
        start_index = (current_page - 1) * ITEMS_PER_PAGE_CHANNELS
        paginated_items = channels_summary[start_index : start_index + ITEMS_PER_PAGE_CHANNELS]
        
        keyboard_rows = []
        for item in paginated_items:
            channel_id = item.get("id") # This is WatchedChannel ID or "direct"
            title = item.get("title", "Unknown Channel")
            count = item.get("count", 0)
            
            card_text = f"📡 {title} — {count} صفقات"
            
            keyboard_rows.append([InlineKeyboardButton("━━━━━━━━━━━━━━━━━━", callback_data="noop")])
            
            callback_data = CallbackBuilder.create(
                CallbackNamespace.MGMT, CallbackAction.SHOW_LIST, 
                f"channel_detail_{channel_id}", 1 # Go to page 1 of this channel's list
            )
            keyboard_rows.append([InlineKeyboardButton(_truncate_text(card_text, 60), callback_data=callback_data)])
        
        # --- Navigation ---
        keyboard_rows.append([InlineKeyboardButton("━━━━━━━━━━━━━━━━━━", callback_data="noop")])
        
        nav_buttons = NavigationBuilder.build_pagination(
            current_page, 
            total_pages,
            base_ns=CallbackNamespace.MGMT,
            base_action=CallbackAction.SHOW_LIST,
            extra_params=(list_type,)
        )
        if nav_buttons:
            keyboard_rows.append(nav_buttons)
            
        keyboard_rows.append([InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, CallbackAction.HUB))])
        return InlineKeyboardMarkup(keyboard_rows)

    except Exception as e:
        logger.error(f"Channels list keyboard build failed: {e}", exc_info=True)
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ Error Loading Channels", callback_data="noop")],
            [InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, CallbackAction.HUB))]
        ])


def build_editable_review_card(parsed_data: Dict[str, Any], channel_name: str = "Unknown") -> InlineKeyboardMarkup:
    """Builds the interactive review card with Activate/Watch buttons."""
    asset = parsed_data.get('asset') or "N/A"
    side = parsed_data.get('side') or "N/A"
    entry = _format_price(parsed_data.get('entry'))
    stop_loss = _format_price(parsed_data.get('stop_loss'))
    targets = parsed_data.get('targets', [])
    target_items = []
    for t in targets:
        price_str = _format_price(t.get('price'))
        close_pct = t.get('close_percent', 0.0)
        item_str = price_str
        if close_pct > 0:
            item_str += f"@{int(close_pct) if close_pct == int(close_pct) else close_pct:.1f}%"
        target_items.append(item_str)
    target_str = ", ".join(target_items) if target_items else "N/A"

    ns = CallbackNamespace.FORWARD_PARSE
    keyboard = [
        [
            InlineKeyboardButton(f"📝 {_truncate_text(f'Asset: {asset}')}",
                                 callback_data=CallbackBuilder.create(ns, CallbackAction.EDIT_FIELD, "asset")),
            InlineKeyboardButton(f"📝 {_truncate_text(f'Side: {side}')}",
                                 callback_data=CallbackBuilder.create(ns, CallbackAction.EDIT_FIELD, "side")),
        ],
        [
            InlineKeyboardButton(f"📝 {_truncate_text(f'Entry: {entry}')}",
                                 callback_data=CallbackBuilder.create(ns, CallbackAction.EDIT_FIELD, "entry")),
            InlineKeyboardButton(f"📝 {_truncate_text(f'SL: {stop_loss}')}",
                                 callback_data=CallbackBuilder.create(ns, CallbackAction.EDIT_FIELD, "stop_loss")),
        ],
        [
            InlineKeyboardButton(f"📝 {_truncate_text(f'Targets: {target_str}', 50)}",
                                 callback_data=CallbackBuilder.create(ns, CallbackAction.EDIT_FIELD, "targets"))
        ],
        [
            InlineKeyboardButton("🚀 Activate Trade",
                                 callback_data=CallbackBuilder.create(ns, CallbackAction.CONFIRM, "activate")),
            InlineKeyboardButton("👁️ Watch Channel Only",
                                 callback_data=CallbackBuilder.create(ns, CallbackAction.WATCH_CHANNEL, "watch")),
        ],
        [
            InlineKeyboardButton(ButtonTexts.CANCEL,
                                 callback_data=CallbackBuilder.create(ns, CallbackAction.CANCEL, "discard")),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


# --- Management Sub-menu Keyboards (REFACTORED for R2 Nav) ---
def analyst_control_panel_keyboard(rec: RecommendationEntity) -> InlineKeyboardMarkup:
    """
    Unified control panel for active recommendations.
    ✅ R2 (Design 3): Matches the new 2x3 button layout.
    """
    rec_id = _get_attr(rec, 'id')
    ns_rec = CallbackNamespace.RECOMMENDATION
    ns_exit = CallbackNamespace.EXIT_STRATEGY
    ns_pos = CallbackNamespace.POSITION

    keyboard = [
        [ 
            InlineKeyboardButton("🔄 تحديث السعر", callback_data=CallbackBuilder.create(ns_pos, CallbackAction.SHOW, 'rec', rec_id)),
            InlineKeyboardButton("💰 Partial Close", callback_data=CallbackBuilder.create(ns_rec, "partial_close_menu", rec_id)),
        ],
        [ 
            InlineKeyboardButton("❌ Full Close", callback_data=CallbackBuilder.create(ns_rec, "close_menu", rec_id)),
            InlineKeyboardButton("📈 إدارة المخاطرة/الخروج", callback_data=CallbackBuilder.create(ns_exit, "show_menu", rec_id)),
        ],
        [
            InlineKeyboardButton("✏️ تعديل البيانات", callback_data=CallbackBuilder.create(ns_rec, "edit_menu", rec_id)),
        ]
        # (The "Back" button is added by the caller, _send_or_edit_position_panel)
    ]
    return InlineKeyboardMarkup(keyboard)

def build_user_trade_control_keyboard(trade_id: int, orm_status_value: str) -> InlineKeyboardMarkup:
    """
    Keyboard for managing a personal UserTrade.
    ✅ R2 (Design 3): Matches the new button layout.
    """
    ns_pos = CallbackNamespace.POSITION
    
    action_buttons = []
    
    if orm_status_value in (UserTradeStatus.WATCHLIST.value, UserTradeStatus.PENDING_ACTIVATION.value):
        action_buttons.append(
            InlineKeyboardButton("🚀 Activate Trade", 
                                 callback_data=CallbackBuilder.create(ns_pos, CallbackAction.ACTIVATE_TRADE, "trade", trade_id))
        )
        action_buttons.append(
            InlineKeyboardButton("❌ Close Trade", 
                                 callback_data=CallbackBuilder.create(ns_pos, CallbackAction.CLOSE, "trade", trade_id))
        )
    elif orm_status_value == UserTradeStatus.ACTIVATED.value:
        action_buttons.append(
            InlineKeyboardButton("🔄 Refresh Price", 
                                 callback_data=CallbackBuilder.create(ns_pos, CallbackAction.SHOW, "trade", trade_id))
        )
        action_buttons.append(
            InlineKeyboardButton("❌ Close Trade", 
                                 callback_data=CallbackBuilder.create(ns_pos, CallbackAction.CLOSE, "trade", trade_id))
        )

    if not action_buttons and orm_status_value not in [UserTradeStatus.CLOSED.value]:
        action_buttons.append(
            InlineKeyboardButton("🔄 Refresh Status", 
                                 callback_data=CallbackBuilder.create(ns_pos, CallbackAction.SHOW, "trade", trade_id))
        )
    
    # (The "Back" button is added by the caller)
    return InlineKeyboardMarkup([action_buttons]) if action_buttons else None


# --- (Other Keyboards: build_confirmation_keyboard, creation flow, etc. remain) ---

def build_confirmation_keyboard(
    namespace: Union[CallbackNamespace, str],
    item_id: Union[int, str], 
    confirm_text: str = ButtonTexts.CONFIRM,
    cancel_text: str = ButtonTexts.CANCEL
) -> InlineKeyboardMarkup:
    """Builds a generic Yes/No confirmation keyboard using CallbackBuilder."""
    confirm_cb = CallbackBuilder.create(namespace, CallbackAction.CONFIRM, item_id)
    cancel_cb = CallbackBuilder.create(namespace, CallbackAction.CANCEL, item_id)
    return InlineKeyboardMarkup([[ 
        InlineKeyboardButton(confirm_text, callback_data=confirm_cb), 
        InlineKeyboardButton(cancel_text, callback_data=cancel_cb), 
    ]])

def main_creation_keyboard() -> InlineKeyboardMarkup:
    """✅ WEBAPP SUPPORT: Updated with WebApp button as first option."""
    base_url = settings.TELEGRAM_WEBHOOK_URL.rsplit('/', 2)[0] if settings.TELEGRAM_WEBHOOK_URL else "https://YOUR_DOMAIN"
    web_app_url = f"{base_url}/static/create_trade.html"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖥️ Visual Creator (Web App)", web_app=WebAppInfo(url=web_app_url))],
        [InlineKeyboardButton("💬 Interactive Builder", callback_data="method_interactive")],
        [InlineKeyboardButton("⚡️ Quick Command", callback_data="method_quick")],
        [InlineKeyboardButton("📋 Text Editor Paste", callback_data="method_editor")],
    ])

def asset_choice_keyboard(recent_assets: List[str]) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(asset, callback_data=f"asset_{asset}") for asset in recent_assets]
    keyboard = [buttons[i: i + 3] for i in range(0, len(buttons), 3)]
    keyboard.append([InlineKeyboardButton("✍️ Enter New Asset", callback_data="asset_new")])
    return InlineKeyboardMarkup(keyboard)

def side_market_keyboard(current_market: str = "Futures") -> InlineKeyboardMarkup:
    market_display = "Futures" if "futures" in current_market.lower() else "Spot"
    return InlineKeyboardMarkup([
        [ 
            InlineKeyboardButton(f"🟢 LONG / {market_display}", callback_data="side_LONG"),
            InlineKeyboardButton(f"🔴 SHORT / {market_display}", callback_data="side_SHORT") 
        ],
        [InlineKeyboardButton(f"🔄 Change Market (Current: {market_display})", callback_data="side_menu")],
    ])

def market_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 Futures", callback_data="market_Futures"), InlineKeyboardButton("💎 Spot", callback_data="market_Spot")],
        [InlineKeyboardButton("⬅️ Back", callback_data="market_back")],
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
        [InlineKeyboardButton("✅ Publish Now", callback_data=CallbackBuilder.create(ns, "publish", short_token))],
        [
            InlineKeyboardButton("📢 Select Channels", callback_data=CallbackBuilder.create(ns, "choose_channels", short_token)),
            InlineKeyboardButton("📝 Add Notes", callback_data=CallbackBuilder.create(ns, "add_notes", short_token))
        ],
        [InlineKeyboardButton("❌ Cancel Creation", callback_data=CallbackBuilder.create(ns, "cancel", short_token))],
    ])

def build_channel_picker_keyboard(review_token: str, channels: Iterable[Any], selected_ids: Set[int], page: int = 1, per_page: int = 6) -> InlineKeyboardMarkup:
    """Builds the paginated channel selection keyboard using CallbackBuilder."""
    try:
        ch_list = list(channels)
        total = len(ch_list)
        total_pages = max(1, math.ceil(total / per_page))
        page = max(1, min(page, total_pages))
        
        start_idx, end_idx = (page - 1) * per_page, page * per_page
        page_items = ch_list[start_idx:end_idx]
        
        rows = []
        short_token = review_token[:12]
        ns = CallbackNamespace.PUBLICATION
        
        for ch in page_items:
            tg_chat_id = int(_get_attr(ch, 'telegram_channel_id', 0))
            if not tg_chat_id: 
                continue
            label = _truncate_text(_get_attr(ch, 'title') or f"Channel {tg_chat_id}", 25)
            status = "✅" if tg_chat_id in selected_ids else ("☑️" if _get_attr(ch, 'is_active', False) else "❌")
            callback_data = CallbackBuilder.create(ns, CallbackAction.TOGGLE, short_token, tg_chat_id, page)
            rows.append([InlineKeyboardButton(f"{status} {label}", callback_data=callback_data)])
        
        nav_buttons = []
        if page > 1: 
            nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=CallbackBuilder.create(ns, "nav", short_token, page - 1)))
        if total_pages > 1: 
            nav_buttons.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
        if page < total_pages: 
            nav_buttons.append(InlineKeyboardButton("➡️", callback_data=CallbackBuilder.create(ns, "nav", short_token, page + 1)))
        
        if nav_buttons:
            rows.append(nav_buttons)
        
        rows.append([
            InlineKeyboardButton("🚀 Publish Selected", callback_data=CallbackBuilder.create(ns, CallbackAction.CONFIRM, short_token)),
            InlineKeyboardButton("⬅️ Back to Review", callback_data=CallbackBuilder.create(ns, CallbackAction.BACK, short_token))
        ])
        return InlineKeyboardMarkup(rows)
    except Exception as e:
        logger.error(f"Error building channel picker: {e}", exc_info=True)
        return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Error - Back to Review", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.BACK, review_token[:12]))]])

def public_channel_keyboard(rec_id: int, bot_username: Optional[str]) -> Optional[InlineKeyboardMarkup]:
    """
    Creates the keyboard for public channel posts.
    ✅ FIX: Added 'Refresh' button alongside 'Track Signal'.
    """
    buttons = []
    
    # 1. Track Signal (Deep Link)
    if bot_username:
        track_url = f"https://t.me/{bot_username}?start=track_{rec_id}"
        buttons.append(InlineKeyboardButton("📊 Track Signal", url=track_url))
    
    # 2. Refresh Button (Callback) - ✅ THE ONLY ADDITION
    refresh_cb = CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "refresh", rec_id)
    buttons.append(InlineKeyboardButton("🔄 Refresh", callback_data=refresh_cb))
    
    return InlineKeyboardMarkup([buttons]) if buttons else None

def build_subscription_keyboard(channel_link: Optional[str]) -> Optional[InlineKeyboardMarkup]:
    if channel_link: 
        return InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Join Channel", url=channel_link)]])
    return None


def build_close_options_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    ns = CallbackNamespace.RECOMMENDATION
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📉 Close at Market", callback_data=CallbackBuilder.create(ns, "close_market", rec_id))],
        [InlineKeyboardButton("✍️ Close at Price", callback_data=CallbackBuilder.create(ns, "close_manual", rec_id))],
        # (Back button added by caller)
    ])

def build_trade_data_edit_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    ns = CallbackNamespace.RECOMMENDATION
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Edit Entry Price", callback_data=CallbackBuilder.create(ns, "edit_entry", rec_id))],
        [InlineKeyboardButton("🛑 Edit Stop Loss", callback_data=CallbackBuilder.create(ns, "edit_sl", rec_id))],
        [InlineKeyboardButton("🎯 Edit Targets", callback_data=CallbackBuilder.create(ns, "edit_tp", rec_id))],
        [InlineKeyboardButton("📝 Edit Notes", callback_data=CallbackBuilder.create(ns, "edit_notes", rec_id))],
        # (Back button added by caller)
    ])

def build_exit_management_keyboard(rec: RecommendationEntity) -> InlineKeyboardMarkup:
    """Builds the exit strategy management panel using CallbackBuilder."""
    rec_id = _get_attr(rec, 'id')
    is_strategy_active = _get_attr(rec, 'profit_stop_active', False)
    ns = CallbackNamespace.EXIT_STRATEGY

    keyboard = [
        [InlineKeyboardButton("⚖️ Move SL to Breakeven", callback_data=CallbackBuilder.create(ns, "move_to_be", rec_id))],
        [InlineKeyboardButton("🔒 Set Fixed Profit Stop", callback_data=CallbackBuilder.create(ns, "set_fixed", rec_id))],
        [InlineKeyboardButton("📈 Set Trailing Stop", callback_data=CallbackBuilder.create(ns, "set_trailing", rec_id))],
    ]
    if is_strategy_active:
        keyboard.append([InlineKeyboardButton("❌ Cancel Active Strategy", callback_data=CallbackBuilder.create(ns, "cancel", rec_id))])
    
    # (Back button added by caller)
    return InlineKeyboardMarkup(keyboard)

def build_partial_close_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    """Builds the partial close keyboard using CallbackBuilder."""
    ns = CallbackNamespace.RECOMMENDATION
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Close 25%", callback_data=CallbackBuilder.create(ns, CallbackAction.PARTIAL, rec_id, "25"))],
        [InlineKeyboardButton("💰 Close 50%", callback_data=CallbackBuilder.create(ns, CallbackAction.PARTIAL, rec_id, "50"))],
        [InlineKeyboardButton("✍️ Custom %", callback_data=CallbackBuilder.create(ns, "partial_close_custom", rec_id))],
        # (Back button added by caller)
    ])
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/keyboards.py ---