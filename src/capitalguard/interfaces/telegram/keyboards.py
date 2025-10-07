# src/capitalguard/interfaces/telegram/keyboards.py (v14.0.3 - FINAL & ROBUST)
import math
from typing import List, Iterable, Set, Optional, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from capitalguard.domain.entities import Recommendation, RecommendationStatus, ExitStrategy
from capitalguard.application.services.price_service import PriceService
from capitalguard.interfaces.telegram.ui_texts import _pct

ITEMS_PER_PAGE = 8

def _get_attr(obj: Any, attr: str, default: Any = None) -> Any:
    """
    Safely gets an attribute from an object, supporting both direct access
    and nested .value access for Value Objects.
    """
    if hasattr(obj, attr):
        val = getattr(obj, attr)
        if hasattr(val, 'value'):
            return val.value
        return val
    return default

def main_creation_keyboard() -> InlineKeyboardMarkup:
    """Displays the main menu for choosing a recommendation creation method."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Interactive Builder (/new)", callback_data="method_interactive")],
        [InlineKeyboardButton("⚡️ Quick Command (/rec)", callback_data="method_quick")],
        [InlineKeyboardButton("📋 Text Editor (/editor)", callback_data="method_editor")],
    ])


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
        market = _get_attr(item, 'market', 'Futures')

        button_text = f"#{rec_id} - {asset} ({side})"
        
        # Logic for RecommendationEntity
        if isinstance(item, Recommendation):
            if status == RecommendationStatus.PENDING:
                status_icon = "⏳"
                button_text = f"{status_icon} {button_text} | Pending"
            elif status == RecommendationStatus.ACTIVE:
                live_price = await price_service.get_cached_price(asset, market)
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
            live_price = await price_service.get_cached_price(asset, market)
            if live_price is not None:
                pnl = _pct(entry, float(live_price), side)
                status_icon = "🟢" if pnl >= 0 else "🔴"
                button_text = f"{status_icon} {button_text} | PnL: {pnl:+.2f}%"
            else:
                button_text = f"{status_icon} {button_text} | Active"

        # Both types of items should lead to a control panel
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


def public_channel_keyboard(rec_id: int, bot_username: str) -> InlineKeyboardMarkup:
    """Builds the keyboard for a public channel message."""
    buttons = [
        InlineKeyboardButton("🔄 Refresh Live Data", callback_data=f"rec:update_public:{rec_id}")
    ]
    
    if bot_username:
        buttons.insert(0, InlineKeyboardButton("📊 Track Signal", url=f"https://t.me/{bot_username}?start=track_{rec_id}"))

    return InlineKeyboardMarkup([buttons])


def analyst_control_panel_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    """
    Dynamically builds the control panel based on the recommendation's status.
    """
    rec_id = rec.id
    
    if rec.status == RecommendationStatus.PENDING:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel Recommendation", callback_data=f"rec:cancel_pending:{rec_id}")],
            [InlineKeyboardButton("⬅️ Back to List", callback_data=f"open_nav:page:1")],
        ])
    
    # Default keyboard for ACTIVE recommendations
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Refresh Price", callback_data=f"rec:show_panel:{rec_id}"),
            InlineKeyboardButton("✏️ Edit", callback_data=f"rec:edit_menu:{rec_id}"),
        ],
        [
            InlineKeyboardButton("📈 Exit Strategy", callback_data=f"rec:strategy_menu:{rec_id}"),
            InlineKeyboardButton("💰 Partial Profit", callback_data=f"rec:close_partial:{rec_id}"),
        ],
        [
            InlineKeyboardButton("❌ Close Full Position", callback_data=f"rec:close_menu:{rec_id}")
        ],
        [InlineKeyboardButton("⬅️ Back to Recommendations List", callback_data=f"open_nav:page:1")],
    ])


def build_close_options_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📉 Close at Market Price", callback_data=f"rec:close_market:{rec_id}")],
        [InlineKeyboardButton("✍️ Close at Specific Price", callback_data=f"rec:close_manual:{rec_id}")],
        [InlineKeyboardButton("⬅️ Cancel", callback_data=f"rec:back_to_main:{rec_id}")],
    ])


def analyst_edit_menu_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛑 Edit Stop Loss", callback_data=f"rec:edit_sl:{rec_id}"),
            InlineKeyboardButton("🎯 Edit Targets", callback_data=f"rec:edit_tp:{rec_id}"),
        ],
        [InlineKeyboardButton("⬅️ Back to Control Panel", callback_data=f"rec:back_to_main:{rec_id}")],
    ])


def build_exit_strategy_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    rec_id = rec.id
    current_strategy = rec.exit_strategy
    
    auto_close_text = "🎯 Auto-Close at Final TP"
    if current_strategy == ExitStrategy.CLOSE_AT_FINAL_TP: auto_close_text = f"✅ {auto_close_text}"
    
    manual_close_text = "✍️ Manual Close Only"
    if current_strategy == ExitStrategy.MANUAL_CLOSE_ONLY: manual_close_text = f"✅ {manual_close_text}"

    keyboard = [
        [InlineKeyboardButton(auto_close_text, callback_data=f"rec:set_strategy:{rec_id}:{ExitStrategy.CLOSE_AT_FINAL_TP.value}")],
        [InlineKeyboardButton(manual_close_text, callback_data=f"rec:set_strategy:{rec_id}:{ExitStrategy.MANUAL_CLOSE_ONLY.value}")],
        [InlineKeyboardButton("🛡️ Set/Edit Profit Stop", callback_data=f"rec:set_profit_stop:{rec_id}")],
    ]
    
    if getattr(rec, "profit_stop_price", None) is not None:
        keyboard.append([InlineKeyboardButton("🗑️ Remove Profit Stop", callback_data=f"rec:set_profit_stop:{rec_id}:remove")])
        
    keyboard.append([InlineKeyboardButton("⬅️ Back to Control Panel", callback_data=f"rec:back_to_main:{rec_id}")])
    
    return InlineKeyboardMarkup(keyboard)


def confirm_close_keyboard(rec_id: int, exit_price: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Confirm Close", callback_data=f"rec:confirm_close:{rec_id}:{exit_price}"),
            InlineKeyboardButton("❌ Go Back", callback_data=f"rec:cancel_close:{rec_id}"),
        ]]
    )


def asset_choice_keyboard(recent_assets: List[str]) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(asset, callback_data=f"asset_{asset}") for asset in recent_assets]
    keyboard_layout = [buttons[i: i + 3] for i in range(0, len(buttons), 3)]
    keyboard_layout.append([InlineKeyboardButton("✍️ Type a New Asset", callback_data="asset_new")])
    return InlineKeyboardMarkup(keyboard_layout)


def side_market_keyboard(current_market: str = "Futures") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"LONG / {current_market}", callback_data=f"side_LONG"),
            InlineKeyboardButton(f"SHORT / {current_market}", callback_data=f"side_SHORT"),
        ],
        [InlineKeyboardButton(f"🔄 Change Market (Current: {current_market})", callback_data="change_market_menu")],
    ])


def market_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Futures", callback_data="market_Futures"), InlineKeyboardButton("Spot", callback_data="market_Spot")],
        [InlineKeyboardButton("⬅️ Back", callback_data="market_back")],
    ])


def order_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Market (Instant Entry)", callback_data="type_MARKET")],
        [InlineKeyboardButton("Limit (Wait for better price)", callback_data="type_LIMIT")],
        [InlineKeyboardButton("Stop Market (Entry after breakout)", callback_data="type_STOP_MARKET")],
    ])


def review_final_keyboard(review_token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Publish to Active Channels", callback_data=f"rec:publish:{review_token}")],
        [
            InlineKeyboardButton("📢 Choose Channels", callback_data=f"rec:choose_channels:{review_token}"),
            InlineKeyboardButton("📝 Add/Edit Notes", callback_data=f"rec:add_notes:{review_token}"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"rec:cancel:{review_token}")],
    ])


def build_channel_picker_keyboard(
    review_token: str,
    channels: Iterable[Any],
    selected_ids: Set[int],
    page: int = 1,
    per_page: int = 5,
) -> InlineKeyboardMarkup:
    ch_list = list(channels)
    total = len(ch_list)
    page = max(page, 1)
    start = (page - 1) * per_page
    end = start + per_page
    page_items = ch_list[start:end]

    rows: List[List[InlineKeyboardButton]] = []

    for ch in page_items:
        tg_chat_id = int(ch.telegram_channel_id)
        label = ch.title or (f"@{ch.username}" if ch.username else str(tg_chat_id))
        mark = "✅" if tg_chat_id in selected_ids else "☑️"
        rows.append([InlineKeyboardButton(f"{mark} {label}", callback_data=f"pubsel:toggle:{review_token}:{tg_chat_id}:{page}")])

    nav: List[InlineKeyboardButton] = []
    max_page = max(1, math.ceil(total / per_page))
    if page > 1: nav.append(InlineKeyboardButton("⬅️", callback_data=f"pubsel:nav:{review_token}:{page-1}"))
    if max_page > 1: nav.append(InlineKeyboardButton(f"Page {page}/{max_page}", callback_data="noop"))
    if page < max_page: nav.append(InlineKeyboardButton("➡️", callback_data=f"pubsel:nav:{review_token}:{page+1}"))
    if nav: rows.append(nav)

    rows.append([
        InlineKeyboardButton("🚀 Publish to Selected", callback_data=f"pubsel:confirm:{review_token}"),
        InlineKeyboardButton("⬅️ Back", callback_data=f"pubsel:back:{review_token}"),
    ])

    return InlineKeyboardMarkup(rows)


def build_subscription_keyboard(channel_link: Optional[str]) -> Optional[InlineKeyboardMarkup]:
    if channel_link:
        return InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Join Channel", url=channel_link)]])
    return None


def build_signal_tracking_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔔 Notify on TP1", callback_data=f"track:notify_tp1:{rec_id}"),
            InlineKeyboardButton("🔔 Notify on SL", callback_data=f"track:notify_sl:{rec_id}")
        ],
        [
            InlineKeyboardButton("➕ Add to My Portfolio (Coming Soon)", callback_data=f"track:add_portfolio:{rec_id}")
        ]
    ])