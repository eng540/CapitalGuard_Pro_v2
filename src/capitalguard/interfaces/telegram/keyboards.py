# --- src/capitalguard/interfaces/telegram/keyboards.py ---
# src/capitalguard/interfaces/telegram/keyboards.py (v21.14 - Editable Review Card)
"""
Builds all Telegram keyboards for the bot.
✅ NEW: Added `build_editable_review_card` for the interactive parsing review flow.
✅ Includes previous fixes for asyncio, callbacks, and structure.
"""

import math
import logging
import asyncio
from decimal import Decimal
from typing import List, Iterable, Set, Optional, Any, Dict, Tuple, Union
from enum import Enum

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from capitalguard.domain.entities import Recommendation, RecommendationStatus, ExitStrategy # Keep domain imports
from capitalguard.application.services.price_service import PriceService
# Use internal helpers instead of importing from ui_texts directly if possible
# from capitalguard.interfaces.telegram.ui_texts import _pct # Avoid direct import if helpers exist locally

logger = logging.getLogger(__name__)

# --- Constants ---
ITEMS_PER_PAGE = 8
MAX_BUTTON_TEXT_LENGTH = 40
MAX_CALLBACK_DATA_LENGTH = 64 # Telegram limit is 64 bytes

# --- Internal Helpers (Duplicated from ui_texts/trade_service for decoupling if needed) ---
# It's better practice to have these in a shared utility module if used across interfaces
def _to_decimal(value: Any, default: Decimal = Decimal('0')) -> Decimal:
    if isinstance(value, Decimal): return value if value.is_finite() else default
    if value is None: return default
    try:
        d = Decimal(str(value))
        return d if d.is_finite() else default
    except Exception: return default

def _format_price(price: Any) -> str:
    price_dec = _to_decimal(price)
    return "N/A" if not price_dec.is_finite() else f"{price_dec:g}"

def _pct(entry: Any, target_price: Any, side: str) -> float:
    try:
        entry_dec = _to_decimal(entry)
        target_dec = _to_decimal(target_price)
        if not entry_dec.is_finite() or entry_dec.is_zero() or not target_dec.is_finite(): return 0.0
        side_upper = (str(side) or "").upper()
        if side_upper == "LONG": pnl = ((target_dec / entry_dec) - 1) * 100
        elif side_upper == "SHORT": pnl = ((entry_dec / target_dec) - 1) * 100
        else: return 0.0
        return float(pnl)
    except Exception: return 0.0

def _truncate_text(text: str, max_length: int = MAX_BUTTON_TEXT_LENGTH) -> str:
    """Truncates text with ellipsis if too long."""
    text = str(text or "")
    return text if len(text) <= max_length else text[:max_length-3] + "..."

def _get_attr(obj: Any, attr: str, default: Any = None) -> Any:
    """Safely gets attribute, handles domain objects with .value."""
    val = getattr(obj, attr, default)
    # Check if val itself has a 'value' attribute (like domain value objects)
    return getattr(val, 'value', val)


# --- Core Callback Architecture ---
class CallbackNamespace(Enum):
    POSITION = "pos"
    RECOMMENDATION = "rec"
    EXIT_STRATEGY = "exit"
    NAVIGATION = "nav"
    PUBLICATION = "pub"
    FORWARD_PARSE = "fwd_parse" # Namespace for parsing review actions
    FORWARD_CONFIRM = "fwd_confirm" # Namespace for final confirmation after parsing
    SAVE_TEMPLATE = "save_template" # Namespace for template saving confirmation
    MGMT = "mgmt" # Generic management actions like cancel
    # Add other namespaces as needed...

class CallbackAction(Enum):
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
    # Add other actions as needed...

class CallbackBuilder:
    @staticmethod
    def create(namespace: Union[CallbackNamespace, str], action: Union[CallbackAction, str], *params) -> str:
        """Builds a callback data string, ensuring it fits Telegram limits."""
        ns_val = namespace.value if isinstance(namespace, CallbackNamespace) else namespace
        act_val = action.value if isinstance(action, CallbackAction) else action
        param_str = ":".join(map(str, params))
        base = f"{ns_val}:{act_val}"
        if param_str: base = f"{base}:{param_str}"

        # Ensure length constraint
        if len(base.encode('utf-8')) > MAX_CALLBACK_DATA_LENGTH:
            # Simple truncation strategy - might need refinement
            # Try removing middle params first if possible?
            logger.warning(f"Callback data longer than {MAX_CALLBACK_DATA_LENGTH} bytes, truncating: {base}")
            # Truncate keeping namespace and action visible
            allowed_param_len = MAX_CALLBACK_DATA_LENGTH - len(f"{ns_val}:{act_val}:".encode('utf-8')) - 3 # Reserve for "..."
            if allowed_param_len > 0:
                 base = f"{ns_val}:{act_val}:{param_str[:allowed_param_len]}..."
            else: # Even namespace:action is too long (unlikely)
                 base = base[:MAX_CALLBACK_DATA_LENGTH]
        return base

    @staticmethod
    def parse(callback_data: str) -> Dict[str, Any]:
        """Parses a callback data string."""
        try:
            parts = callback_data.split(':')
            # Handle potential truncated "..."
            if parts[-1].endswith("..."):
                 parts = parts[:-1] # Ignore truncated part for parsing logic
                 logger.warning(f"Parsing potentially truncated callback data: {callback_data}")

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
    """Legacy helper, use CallbackBuilder.parse instead."""
    parsed = CallbackBuilder.parse(callback_data)
    # Reconstruct list for compatibility if needed, but prefer using parsed dict
    parts = []
    if parsed.get('namespace'): parts.append(parsed['namespace'])
    if parsed.get('action'): parts.append(parsed['action'])
    if parsed.get('params'): parts.extend(parsed['params'])
    return parts


# --- UI Constants ---
class StatusIcons:
    PENDING = "⏳"; ACTIVE = "▶️"; PROFIT = "🟢"; LOSS = "🔴"; CLOSED = "🏁"; ERROR = "⚠️"
    BREAK_EVEN = "🛡️"; SHADOW = "👻"; # Added from previous versions

class ButtonTexts:
    BACK_TO_LIST = "⬅️ العودة للقائمة"; BACK_TO_MAIN = "⬅️ العودة للوحة التحكم";
    PREVIOUS = "⬅️ السابق"; NEXT = "التالي ➡️"; CONFIRM = "✅ تأكيد"; CANCEL = "❌ إلغاء";

# --- Status & Navigation Logic ---
class StatusDeterminer:
    @staticmethod
    def determine_icon(item: Any, live_price: Optional[float] = None) -> str:
        """Determines the status icon based on item state and live price."""
        try:
            status = _get_attr(item, 'status')
            status_value = status.value if hasattr(status, 'value') else status # Handle Enum or string

            if status_value in [RecommendationStatus.PENDING.value, 'PENDING']: return StatusIcons.PENDING
            if status_value in [RecommendationStatus.CLOSED.value, 'CLOSED']: return StatusIcons.CLOSED

            if status_value in [RecommendationStatus.ACTIVE.value, 'ACTIVE', 'OPEN']: # Check for UserTrade 'OPEN'
                entry_dec = _to_decimal(_get_attr(item, 'entry'))
                sl_dec = _to_decimal(_get_attr(item, 'stop_loss'))

                # Check for Break Even (SL very close to Entry)
                if entry_dec > 0 and sl_dec > 0 and abs(entry_dec - sl_dec) / entry_dec < Decimal('0.0005'): # Within 0.05%
                    return StatusIcons.BREAK_EVEN

                if live_price is not None:
                    side = _get_attr(item, 'side')
                    if entry_dec > 0:
                        pnl = _pct(entry_dec, live_price, side)
                        return StatusIcons.PROFIT if pnl >= 0 else StatusIcons.LOSS
                return StatusIcons.ACTIVE # Default for ACTIVE if no price or PNL calc fails
            return StatusIcons.ERROR # Unknown status
        except Exception as e:
             logger.warning(f"Status determination failed: {e}")
             return StatusIcons.ERROR

class NavigationBuilder:
    @staticmethod
    def build_pagination(current_page: int, total_pages: int, # Optional base namespace/params?
                         base_ns: CallbackNamespace = CallbackNamespace.NAVIGATION,
                         base_action: CallbackAction = CallbackAction.NAVIGATE,
                         extra_params: Tuple = ()
                         ) -> List[List[InlineKeyboardButton]]:
        """Builds pagination buttons using CallbackBuilder."""
        buttons = []
        if current_page > 1:
            buttons.append(InlineKeyboardButton(
                ButtonTexts.PREVIOUS,
                callback_data=CallbackBuilder.create(base_ns, base_action, current_page - 1, *extra_params)
            ))
        if total_pages > 1:
            buttons.append(InlineKeyboardButton(f"📄 {current_page}/{total_pages}", callback_data="noop")) # No action
        if current_page < total_pages:
            buttons.append(InlineKeyboardButton(
                ButtonTexts.NEXT,
                callback_data=CallbackBuilder.create(base_ns, base_action, current_page + 1, *extra_params)
            ))
        return [buttons] if buttons else []

# --- Keyboard Factories ---

async def build_open_recs_keyboard(items: List[Any], current_page: int, price_service: PriceService) -> InlineKeyboardMarkup:
    """Builds the paginated keyboard for open recommendations/trades (v21.13 logic)."""
    # (Implementation remains the same as v21.13 provided previously)
    try:
        total_items = len(items)
        total_pages = math.ceil(total_items / ITEMS_PER_PAGE) or 1
        current_page = max(1, min(current_page, total_pages))
        start_index = (current_page - 1) * ITEMS_PER_PAGE
        paginated_items = items[start_index : start_index + ITEMS_PER_PAGE]

        # Fetch prices concurrently
        price_tasks = {}
        assets_to_fetch = set()
        for item in paginated_items:
            asset = _get_attr(item, 'asset')
            if asset: assets_to_fetch.add(asset)

        price_results = await asyncio.gather(*[
             price_service.get_cached_price(asset, _get_attr(item, 'market', 'Futures')) # Assuming market attr exists
             for asset in assets_to_fetch
        ], return_exceptions=True)

        prices_map = dict(zip(assets_to_fetch, price_results))

        keyboard_rows = []
        for item in paginated_items:
            rec_id, asset, side = _get_attr(item, 'id'), _get_attr(item, 'asset'), _get_attr(item, 'side')
            live_price_res = prices_map.get(asset)
            live_price = live_price_res if not isinstance(live_price_res, Exception) else None

            status_icon = StatusDeterminer.determine_icon(item, live_price)
            button_text = f"#{rec_id} - {asset} ({side})"

            if live_price is not None and status_icon in [StatusIcons.PROFIT, StatusIcons.LOSS]:
                pnl = _pct(_get_attr(item, 'entry'), live_price, side)
                button_text = f"{status_icon} {button_text} | PnL: {pnl:+.2f}%"
            else:
                 button_text = f"{status_icon} {button_text}"

            item_type = 'trade' if getattr(item, 'is_user_trade', False) else 'rec'
            callback_data = CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, item_type, rec_id)
            keyboard_rows.append([InlineKeyboardButton(_truncate_text(button_text), callback_data=callback_data)])

        # Use NavigationBuilder for pagination
        keyboard_rows.extend(NavigationBuilder.build_pagination(current_page, total_pages))
        # Add refresh button
        keyboard_rows.append([InlineKeyboardButton("🔄 تحديث القائمة", callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, current_page))])

        return InlineKeyboardMarkup(keyboard_rows)
    except Exception as e:
        logger.error(f"Open recs keyboard build failed: {e}", exc_info=True)
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ خطأ في تحميل البيانات", callback_data="noop")],
            [InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1))]
         ])


# ✅ NEW Keyboard for Editable Review Card
def build_editable_review_card(parsed_data: Dict[str, Any]) -> InlineKeyboardMarkup:
    """Builds the interactive review card with edit buttons for parsed data."""
    # Assume parsed_data keys: asset, side, entry(D), stop_loss(D), targets(List[Dict{price:D, %}])
    asset = parsed_data.get('asset', 'N/A')
    side = parsed_data.get('side', 'N/A')
    entry = _format_price(parsed_data.get('entry'))
    stop_loss = _format_price(parsed_data.get('stop_loss'))
    targets = parsed_data.get('targets', [])

    # Format targets compactly
    target_str = ", ".join([
        f"{_format_price(t['price'])}{'@'+str(int(t['close_percent']))+'%' if t.get('close_percent',0) > 0 else ''}"
        for t in targets
    ])

    ns = CallbackNamespace.FORWARD_PARSE # Use the specific namespace

    keyboard = [
        # Row 1: Asset & Side Edit
        [
            InlineKeyboardButton(f"📝 {_truncate_text('Asset: '+asset)}",
                                 callback_data=CallbackBuilder.create(ns, CallbackAction.EDIT_FIELD, "asset")),
            InlineKeyboardButton(f"📝 {_truncate_text('Side: '+side)}",
                                 callback_data=CallbackBuilder.create(ns, CallbackAction.EDIT_FIELD, "side")),
        ],
        # Row 2: Entry & Stop Loss Edit
        [
            InlineKeyboardButton(f"📝 {_truncate_text('Entry: '+entry)}",
                                 callback_data=CallbackBuilder.create(ns, CallbackAction.EDIT_FIELD, "entry")),
            InlineKeyboardButton(f"📝 {_truncate_text('SL: '+stop_loss)}",
                                 callback_data=CallbackBuilder.create(ns, CallbackAction.EDIT_FIELD, "stop_loss")),
        ],
        # Row 3: Targets Edit
        [
            InlineKeyboardButton(f"📝 {_truncate_text('Targets: '+target_str, 50)}", # Allow more length for targets
                                 callback_data=CallbackBuilder.create(ns, CallbackAction.EDIT_FIELD, "targets"))
        ],
        # Row 4: Actions
        [
            InlineKeyboardButton(ButtonTexts.CONFIRM + " & Track",
                                 callback_data=CallbackBuilder.create(ns, CallbackAction.CONFIRM, "save")),
            InlineKeyboardButton(ButtonTexts.CANCEL,
                                 callback_data=CallbackBuilder.create(ns, CallbackAction.CANCEL, "discard")),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


# --- Existing Keyboard Factories (Keep as they are, ensure they use CallbackBuilder) ---

def analyst_control_panel_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    """Unified control panel for active recommendations (v21.13 structure)."""
    rec_id = _get_attr(rec, 'id')
    ns_rec = CallbackNamespace.RECOMMENDATION
    ns_pos = CallbackNamespace.POSITION
    ns_exit = CallbackNamespace.EXIT_STRATEGY
    ns_nav = CallbackNamespace.NAVIGATION

    # Only show controls if ACTIVE
    if _get_attr(rec, 'status') != RecommendationStatus.ACTIVE:
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
            # Refresh button
            InlineKeyboardButton("🔄 تحديث السعر", callback_data=CallbackBuilder.create(ns_pos, CallbackAction.SHOW, "trade", trade_id)),
            # Close button (triggers conversation)
            InlineKeyboardButton("❌ إغلاق الصفقة", callback_data=CallbackBuilder.create(ns_pos, CallbackAction.CLOSE, "trade", trade_id))
        ],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(ns_nav, CallbackAction.NAVIGATE, 1))],
    ])

def build_confirmation_keyboard(
    namespace: Union[CallbackNamespace, str],
    item_id: Union[int, str], # Can be attempt_id or rec_id etc.
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


# --- Other keyboards (main_creation, asset_choice, side_market, etc.) ---
# Keep these as they were defined previously, ensuring they use CallbackBuilder where appropriate.
# Example modification for review_final_keyboard:
def review_final_keyboard(review_token: str) -> InlineKeyboardMarkup:
    short_token = review_token[:12] # Keep short token logic
    ns = CallbackNamespace.RECOMMENDATION # Use enum
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نشر الآن", callback_data=CallbackBuilder.create(ns, "publish", short_token))],
        [
            InlineKeyboardButton("📢 اختيار القنوات", callback_data=CallbackBuilder.create(ns, "choose_channels", short_token)),
            InlineKeyboardButton("📝 إضافة ملاحظات", callback_data=CallbackBuilder.create(ns, "add_notes", short_token))
        ],
        [InlineKeyboardButton("❌ إلغاء", callback_data=CallbackBuilder.create(ns, "cancel", short_token))],
    ])

# Ensure all other keyboard functions (build_close_options, build_exit_management, etc.)
# are updated to use CallbackBuilder.create consistently.

# --- Keep the rest of the keyboard functions (unchanged unless needing CallbackBuilder update) ---
# ... (public_channel_keyboard, build_trade_data_edit_keyboard, build_exit_management_keyboard, etc.) ...
# Ensure correct imports and helper function availability within this file.

# --- END of keyboards update ---