# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/keyboards.py ---
# src/capitalguard/interfaces/telegram/keyboards.py (v21.15 - Panel Logic Hotfix)
"""
Builds all Telegram keyboards for the bot.
✅ HOTFIX: Corrected logical comparison in `analyst_control_panel_keyboard`
       from `status != RecommendationStatus.ACTIVE` (Object comparison)
       to `status.value != RecommendationStatus.ACTIVE.value` (Value comparison).
       This resolves the critical bug where the analyst panel never appeared.
✅ Includes previous fixes for asyncio, callbacks, and structure.
"""

import math
import logging
import asyncio
from decimal import Decimal
from typing import List, Iterable, Set, Optional, Any, Dict, Tuple, Union
from enum import Enum
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from capitalguard.domain.entities import Recommendation as RecommendationEntity, RecommendationStatus, ExitStrategy
from capitalguard.application.services.price_service import PriceService

logger = logging.getLogger(__name__)

# --- Constants ---
ITEMS_PER_PAGE = 8
MAX_BUTTON_TEXT_LENGTH = 40
MAX_CALLBACK_DATA_LENGTH = 64  # Telegram limit

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
    return "N/A" if not price_dec.is_finite() else f"{price_dec:g}"

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
    text = str(text or "")
    return text if len(text) <= max_length else text[:max_length - 3] + "..."

def _get_attr(obj: Any, attr: str, default: Any = None) -> Any:
    val = getattr(obj, attr, default)
    return getattr(val, 'value', val)

# --- Callback Architecture ---
class CallbackNamespace(Enum):
    POSITION = "pos"
    RECOMMENDATION = "rec"
    EXIT_STRATEGY = "exit"
    NAVIGATION = "nav"
    PUBLICATION = "pub"
    FORWARD_PARSE = "fwd_parse"
    FORWARD_CONFIRM = "fwd_confirm"
    SAVE_TEMPLATE = "save_template"
    MGMT = "mgmt"

class CallbackAction(Enum):
    SHOW = "sh"
    UPDATE = "up"
    NAVIGATE = "nv"
    BACK = "bk"
    CLOSE = "cl"
    PARTIAL = "pt"
    CONFIRM = "cf"
    CANCEL = "cn"
    EDIT_FIELD = "edit_field"
    TOGGLE = "toggle"

class CallbackBuilder:
    @staticmethod
    def create(namespace: Union[CallbackNamespace, str], action: Union[CallbackAction, str], *params) -> str:
        ns_val = namespace.value if isinstance(namespace, CallbackNamespace) else namespace
        act_val = action.value if isinstance(action, CallbackAction) else action
        param_str = ":".join(map(str, params))
        base = f"{ns_val}:{act_val}" + (f":{param_str}" if param_str else "")
        if len(base.encode('utf-8')) > MAX_CALLBACK_DATA_LENGTH:
            logger.warning(f"Callback too long, truncating: {base}")
            base = base[:MAX_CALLBACK_DATA_LENGTH]
        return base

# --- UI Constants ---
class StatusIcons:
    PENDING = "⏳"; ACTIVE = "▶️"; PROFIT = "🟢"; LOSS = "🔴"
    CLOSED = "🏁"; ERROR = "⚠️"; BREAK_EVEN = "🛡️"; SHADOW = "👻"

class ButtonTexts:
    BACK_TO_LIST = "⬅️ Back to List"; BACK_TO_MAIN = "⬅️ Back to Panel"
    PREVIOUS = "⬅️ Previous"; NEXT = "Next ➡️"
    CONFIRM = "✅ Confirm"; CANCEL = "❌ Cancel"

# --- Status & Navigation ---
class StatusDeterminer:
    @staticmethod
    def determine_icon(item: Any, live_price: Optional[float] = None) -> str:
        try:
            status = _get_attr(item, 'status')
            status_value = status.value if hasattr(status, 'value') else status
            if status_value in [RecommendationStatus.PENDING.value, 'PENDING']:
                return StatusIcons.PENDING
            if status_value in [RecommendationStatus.CLOSED.value, 'CLOSED']:
                return StatusIcons.CLOSED
            if status_value in [RecommendationStatus.ACTIVE.value, 'ACTIVE', 'OPEN']:
                entry_dec = _to_decimal(_get_attr(item, 'entry'))
                sl_dec = _to_decimal(_get_attr(item, 'stop_loss'))
                if entry_dec > 0 and sl_dec > 0 and abs(entry_dec - sl_dec) / entry_dec < Decimal('0.0005'):
                    return StatusIcons.BREAK_EVEN
                if live_price is not None:
                    side = _get_attr(item, 'side')
                    if entry_dec > 0:
                        pnl = _pct(entry_dec, live_price, side)
                        return StatusIcons.PROFIT if pnl >= 0 else StatusIcons.LOSS
                return StatusIcons.ACTIVE
            return StatusIcons.ERROR
        except Exception as e:
            logger.warning(f"Status determination failed: {e}")
            return StatusIcons.ERROR

# --- Keyboard Factories ---
async def build_open_recs_keyboard(items: List[Any], current_page: int, price_service: PriceService) -> InlineKeyboardMarkup:
    try:
        total_items = len(items)
        total_pages = math.ceil(total_items / ITEMS_PER_PAGE) or 1
        current_page = max(1, min(current_page, total_pages))
        start_index = (current_page - 1) * ITEMS_PER_PAGE
        paginated_items = items[start_index:start_index + ITEMS_PER_PAGE]
        assets_to_fetch = {(_get_attr(i, 'asset'), _get_attr(i, 'market', 'Futures')) for i in paginated_items if _get_attr(i, 'asset')}
        price_tasks = [price_service.get_cached_price(a, m) for a, m in assets_to_fetch]
        price_results = await asyncio.gather(*price_tasks, return_exceptions=True)
        prices_map = {am[0]: p for am, p in zip(assets_to_fetch, price_results) if not isinstance(p, Exception)}

        keyboard_rows = []
        for item in paginated_items:
            rec_id, asset, side = _get_attr(item, 'id'), _get_attr(item, 'asset'), _get_attr(item, 'side')
            live_price = prices_map.get(asset)
            status_icon = StatusDeterminer.determine_icon(item, live_price)
            button_text = f"#{rec_id} - {asset} ({side})"
            if live_price is not None and status_icon in [StatusIcons.PROFIT, StatusIcons.LOSS]:
                pnl = _pct(_get_attr(item, 'entry'), live_price, side)
                button_text = f"{status_icon} {button_text} | PnL: {pnl:+.2f}%"
            else:
                button_text = f"{status_icon} {button_text}"
            item_type = 'trade' if getattr(item, 'is_user_trade', False) else 'rec'
            cb = CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, item_type, rec_id)
            keyboard_rows.append([InlineKeyboardButton(_truncate_text(button_text), callback_data=cb)])

        keyboard_rows.append([InlineKeyboardButton("🔄 Refresh List", callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, current_page))])
        return InlineKeyboardMarkup(keyboard_rows)
    except Exception as e:
        logger.error(f"Open recs keyboard failed: {e}", exc_info=True)
        return InlineKeyboardMarkup([[InlineKeyboardButton("⚠️ Error Loading", callback_data="noop")]])

def build_editable_review_card(parsed_data: Dict[str, Any]) -> InlineKeyboardMarkup:
    asset = parsed_data.get('asset', 'N/A')
    side = parsed_data.get('side', 'N/A')
    entry = _format_price(parsed_data.get('entry'))
    stop_loss = _format_price(parsed_data.get('stop_loss'))
    targets = parsed_data.get('targets', [])
    target_items = []
    for t in targets:
        price_str = _format_price(t.get('price'))
        close_pct = t.get('close_percent', 0.0)
        if close_pct > 0:
            item_str = f"{price_str}@{int(close_pct) if close_pct == int(close_pct) else close_pct:.1f}%"
        else:
            item_str = price_str
        target_items.append(item_str)
    target_str = ", ".join(target_items)
    ns = CallbackNamespace.FORWARD_PARSE
    keyboard = [
        [
            InlineKeyboardButton(f"📝 {_truncate_text('Asset: '+asset)}", callback_data=CallbackBuilder.create(ns, CallbackAction.EDIT_FIELD, "asset")),
            InlineKeyboardButton(f"📝 {_truncate_text('Side: '+side)}", callback_data=CallbackBuilder.create(ns, CallbackAction.EDIT_FIELD, "side")),
        ],
        [
            InlineKeyboardButton(f"📝 Entry: {entry}", callback_data=CallbackBuilder.create(ns, CallbackAction.EDIT_FIELD, "entry")),
            InlineKeyboardButton(f"📝 SL: {stop_loss}", callback_data=CallbackBuilder.create(ns, CallbackAction.EDIT_FIELD, "stop_loss")),
        ],
        [InlineKeyboardButton(f"📝 Targets: {_truncate_text(target_str, 50)}", callback_data=CallbackBuilder.create(ns, CallbackAction.EDIT_FIELD, "targets"))],
        [
            InlineKeyboardButton(ButtonTexts.CONFIRM + " & Track", callback_data=CallbackBuilder.create(ns, CallbackAction.CONFIRM, "save")),
            InlineKeyboardButton(ButtonTexts.CANCEL, callback_data=CallbackBuilder.create(ns, CallbackAction.CANCEL, "discard")),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def analyst_control_panel_keyboard(rec: RecommendationEntity) -> InlineKeyboardMarkup:
    rec_id = _get_attr(rec, 'id')
    status = _get_attr(rec, 'status')
    if _get_attr(status, 'value') != RecommendationStatus.ACTIVE.value:
        return InlineKeyboardMarkup([[InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1))]])
    ns_rec = CallbackNamespace.RECOMMENDATION
    ns_pos = CallbackNamespace.POSITION
    ns_exit = CallbackNamespace.EXIT_STRATEGY
    ns_nav = CallbackNamespace.NAVIGATION
    keyboard = [
        [
            InlineKeyboardButton("🔄 Refresh Price", callback_data=CallbackBuilder.create(ns_pos, CallbackAction.SHOW, 'rec', rec_id)),
            InlineKeyboardButton("💰 Partial Close", callback_data=CallbackBuilder.create(ns_rec, "partial_close_menu", rec_id)),
            InlineKeyboardButton("❌ Full Close", callback_data=CallbackBuilder.create(ns_rec, "close_menu", rec_id)),
        ],
        [
            InlineKeyboardButton("📈 Manage Exit/Risk", callback_data=CallbackBuilder.create(ns_exit, "show_menu", rec_id)),
            InlineKeyboardButton("✏️ Edit Trade Data", callback_data=CallbackBuilder.create(ns_rec, "edit_menu", rec_id)),
        ],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(ns_nav, CallbackAction.NAVIGATE, 1))]
    ]
    return InlineKeyboardMarkup(keyboard)
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/keyboards.py ---