# src/capitalguard/interfaces/telegram/keyboards.py (v14.0.3 - Type Hotfix)
import math
from typing import List, Iterable, Set, Optional, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from capitalguard.domain.entities import Recommendation, RecommendationStatus, ExitStrategy
from capitalguard.application.services.price_service import PriceService
from capitalguard.interfaces.telegram.ui_texts import _pct

ITEMS_PER_PAGE = 8

def _get_attr(obj: Any, attr: str, default: Any = None) -> Any:
    """Safely gets an attribute, supporting both objects and dicts, and nested value objects."""
    if hasattr(obj, attr):
        val = getattr(obj, attr)
        if hasattr(val, 'value'):
            return val.value
        return val
    return default

async def build_open_recs_keyboard(
    items: List[Any], # Can now be RecommendationEntity or UserTrade ORM object
    current_page: int,
    price_service: PriceService,
) -> InlineKeyboardMarkup:
    """Async: Builds the keyboard for open positions, handling both entity and ORM types."""
    keyboard: List[List[InlineKeyboardButton]] = []
    total_items = len(items)
    total_pages = math.ceil(total_items / ITEMS_PER_PAGE) if total_items else 1
    start_index = (current_page - 1) * ITEMS_PER_PAGE
    paginated_items = items[start_index : start_index + ITEMS_PER_PAGE]

    for item in paginated_items:
        # Use the helper to safely access attributes
        rec_id = _get_attr(item, 'id')
        asset = _get_attr(item, 'asset')
        side = _get_attr(item, 'side')
        status = _get_attr(item, 'status')
        entry = float(_get_attr(item, 'entry', 0))

        button_text = f"#{rec_id} - {asset} ({side})"
        
        # Logic for RecommendationEntity
        if isinstance(item, Recommendation):
            if status == RecommendationStatus.PENDING:
                status_icon = "⏳"
                button_text = f"{status_icon} {button_text} | Pending"
            elif status == RecommendationStatus.ACTIVE:
                live_price = await price_service.get_cached_price(asset, _get_attr(item, 'market'))
                if live_price is not None:
                    pnl = _pct(entry, float(live_price), side)
                    status_icon = "🟢" if pnl >= 0 else "🔴"
                    button_text = f"{status_icon} {button_text} | PnL: {pnl:+.2f}%"
                else:
                    status_icon = "▶️"
                    button_text = f"{status_icon} {button_text} | Active"
        # Logic for UserTrade ORM object
        else:
            status_icon = "▶️" # User trades are always considered active for display
            live_price = await price_service.get_cached_price(asset, "Futures") # Assume futures for now
            if live_price is not None:
                pnl = _pct(entry, float(live_price), side)
                status_icon = "🟢" if pnl >= 0 else "🔴"
                button_text = f"{status_icon} {button_text} | PnL: {pnl:+.2f}%"
            else:
                button_text = f"{status_icon} {button_text} | Active"

        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"rec:show_panel:{rec_id}")])

    # Navigation logic remains the same
    nav_buttons: List[List[InlineKeyboardButton]] = []
    page_nav_row: List[InlineKeyboardButton] = []
    if current_page > 1:
        page_nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"open_nav:page:{current_page - 1}"))
    if total_pages > 1:
        page_nav_row.append(InlineKeyboardButton(f"Page {current_page}/{total_pages}", callback_data="noop"))
    if current_page < total_pages:
        page_nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"open_nav:page:{current_page + 1}"))
    if page_nav_row:
        nav_buttons.append(page_nav_row)

    keyboard.extend(nav_buttons)
    return InlineKeyboardMarkup(keyboard)

# ... (Rest of the file remains unchanged)