# --- START OF FINAL, COMPLETE, AND UX-FIXED FILE (Version 14.0.0 - with Cancellation Button) ---
# src/capitalguard/interfaces/telegram/keyboards.py

import math
from typing import List, Iterable, Set, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from capitalguard.domain.entities import Recommendation, RecommendationStatus, ExitStrategy
from capitalguard.application.services.price_service import PriceService
from capitalguard.interfaces.telegram.ui_texts import _pct

ITEMS_PER_PAGE = 8


def main_creation_keyboard() -> InlineKeyboardMarkup:
    """Displays the main menu for choosing a recommendation creation method."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 المنشئ التفاعلي (/new)", callback_data="method_interactive")],
        [InlineKeyboardButton("⚡️ الأمر السريع (/rec)", callback_data="method_quick")],
        [InlineKeyboardButton("📋 المحرر النصي (/editor)", callback_data="method_editor")],
    ])


async def build_open_recs_keyboard(
    items: List[Recommendation],
    current_page: int,
    price_service: PriceService,
) -> InlineKeyboardMarkup:
    """Async: Builds the keyboard for open recommendations, fetching live prices."""
    keyboard: List[List[InlineKeyboardButton]] = []
    total_items = len(items)
    total_pages = math.ceil(total_items / ITEMS_PER_PAGE) if total_items else 1
    start_index = (current_page - 1) * ITEMS_PER_PAGE
    paginated_items = items[start_index: start_index + ITEMS_PER_PAGE]

    for rec in paginated_items:
        display_id = getattr(rec, "analyst_rec_id", rec.id) or rec.id
        button_text = f"#{display_id} - {rec.asset.value} ({rec.side.value})"
        
        if rec.status == RecommendationStatus.PENDING:
            status_icon = "⏳"
            button_text = f"{status_icon} {button_text} | معلقة"
        elif rec.status == RecommendationStatus.ACTIVE:
            if rec.stop_loss.value == rec.entry.value:
                status_icon = "🛡️"
                button_text = f"{status_icon} {button_text} | BE"
            else:
                live_price = await price_service.get_cached_price(rec.asset.value, rec.market)
                if live_price is not None:
                    pnl = _pct(rec.entry.value, float(live_price), rec.side.value)
                    status_icon = "🟢" if pnl >= 0 else "🔴"
                    button_text = f"{status_icon} {button_text} | PnL: {pnl:+.2f}%"
                else:
                    status_icon = "▶️"
                    button_text = f"{status_icon} {button_text} | نشطة"
        
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"rec:show_panel:{rec.id}")])

    nav_buttons: List[List[InlineKeyboardButton]] = []
    page_nav_row: List[InlineKeyboardButton] = []
    if current_page > 1:
        page_nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"open_nav:page:{current_page - 1}"))
    if total_pages > 1:
        page_nav_row.append(InlineKeyboardButton(f"صفحة {current_page}/{total_pages}", callback_data="noop"))
    if current_page < total_pages:
        page_nav_row.append(InlineKeyboardButton("التالي ➡️", callback_data=f"open_nav:page:{current_page + 1}"))
    if page_nav_row:
        nav_buttons.append(page_nav_row)

    keyboard.extend(nav_buttons)
    return InlineKeyboardMarkup(keyboard)


def public_channel_keyboard(rec_id: int, bot_username: str) -> InlineKeyboardMarkup:
    """Builds the keyboard for a public channel message."""
    buttons = [
        InlineKeyboardButton("🔄 تحديث البيانات الحية", callback_data=f"rec:update_public:{rec_id}")
    ]
    
    if bot_username:
        buttons.insert(0, InlineKeyboardButton("📊 تتبّع الإشارة", url=f"https://t.me/{bot_username}?start=track_{rec_id}"))

    return InlineKeyboardMarkup([buttons])


def analyst_control_panel_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    """✅ UPDATED: Now accepts full Recommendation object to show different buttons for PENDING status."""
    rec_id = rec.id
    
    if rec.status == RecommendationStatus.PENDING:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 تحديث السعر", callback_data=f"rec:update_private:{rec.id}")],
            [InlineKeyboardButton("❌ إلغاء التوصية", callback_data=f"rec:cancel_pending:{rec_id}")],
            [InlineKeyboardButton("⬅️ العودة للقائمة", callback_data=f"open_nav:page:1")],
        ])
    
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 تحديث السعر", callback_data=f"rec:update_private:{rec.id}"),
            InlineKeyboardButton("✏️ تعديل", callback_data=f"rec:edit_menu:{rec_id}"),
        ],
        [
            InlineKeyboardButton("📈 استراتيجية الخروج", callback_data=f"rec:strategy_menu:{rec_id}"),
            InlineKeyboardButton("💰 جني ربح جزئي", callback_data=f"rec:close_partial:{rec_id}"),
        ],
        [
            InlineKeyboardButton("❌ إغلاق كلي", callback_data=f"rec:close_menu:{rec_id}")
        ],
        [InlineKeyboardButton("⬅️ العودة لقائمة التوصيات", callback_data=f"open_nav:page:1")],
    ])


def build_close_options_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📉 إغلاق بسعر السوق الآن", callback_data=f"rec:close_market:{rec_id}")],
        [InlineKeyboardButton("✍️ إغلاق بسعر محدد", callback_data=f"rec:close_manual:{rec_id}")],
        [InlineKeyboardButton("⬅️ إلغاء", callback_data=f"rec:back_to_main:{rec_id}")],
    ])


def analyst_edit_menu_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛑 تعديل الوقف", callback_data=f"rec:edit_sl:{rec_id}"),
            InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=f"rec:edit_tp:{rec_id}"),
        ],
        [InlineKeyboardButton("⬅️ العودة للوحة التحكم", callback_data=f"rec:back_to_main:{rec_id}")],
    ])


def build_exit_strategy_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    rec_id = rec.id
    current_strategy = rec.exit_strategy
    
    auto_close_text = "🎯 الإغلاق عند الهدف الأخير"
    if current_strategy == ExitStrategy.CLOSE_AT_FINAL_TP: auto_close_text = f"✅ {auto_close_text}"
    
    manual_close_text = "✍️ الإغلاق اليدوي فقط"
    if current_strategy == ExitStrategy.MANUAL_CLOSE_ONLY: manual_close_text = f"✅ {manual_close_text}"

    keyboard = [
        [InlineKeyboardButton(auto_close_text, callback_data=f"rec:set_strategy:{rec_id}:{ExitStrategy.CLOSE_AT_FINAL_TP.value}")],
        [InlineKeyboardButton(manual_close_text, callback_data=f"rec:set_strategy:{rec_id}:{ExitStrategy.MANUAL_CLOSE_ONLY.value}")],
        [InlineKeyboardButton("🛡️ وضع/تعديل وقف الربح", callback_data=f"rec:set_profit_stop:{rec_id}")],
    ]
    
    if getattr(rec, "profit_stop_price", None) is not None:
        keyboard.append([InlineKeyboardButton("🗑️ إزالة وقف الربح", callback_data=f"rec:set_profit_stop:{rec_id}:remove")])
        
    keyboard.append([InlineKeyboardButton("⬅️ العودة للوحة التحكم", callback_data=f"rec:back_to_main:{rec_id}")])
    
    return InlineKeyboardMarkup(keyboard)


def confirm_close_keyboard(rec_id: int, exit_price: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ تأكيد الإغلاق", callback_data=f"rec:confirm_close:{rec_id}:{exit_price}"),
            InlineKeyboardButton("❌ تراجع", callback_data=f"rec:cancel_close:{rec_id}"),
        ]]
    )


def asset_choice_keyboard(recent_assets: List[str]) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(asset, callback_data=f"asset_{asset}") for asset in recent_assets]
    keyboard_layout = [buttons[i: i + 3] for i in range(0, len(buttons), 3)]
    keyboard_layout.append([InlineKeyboardButton("✍️ اكتب أصلاً جديدًا", callback_data="asset_new")])
    return InlineKeyboardMarkup(keyboard_layout)


def side_market_keyboard(current_market: str = "Futures") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"LONG / {current_market}", callback_data=f"side_LONG"),
            InlineKeyboardButton(f"SHORT / {current_market}", callback_data=f"side_SHORT"),
        ],
        [InlineKeyboardButton(f"🔄 تغيير السوق (الحالي: {current_market})", callback_data="change_market_menu")],
    ])


def market_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Futures", callback_data="market_Futures"), InlineKeyboardButton("Spot", callback_data="market_Spot")],
        [InlineKeyboardButton("⬅️ عودة", callback_data="market_back")],
    ])


def order_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Market (دخول فوري)", callback_data="type_MARKET")],
        [InlineKeyboardButton("Limit (انتظار سعر أفضل)", callback_data="type_LIMIT")],
        [InlineKeyboardButton("Stop Market (دخول بعد اختراق)", callback_data="type_STOP_MARKET")],
    ])


def review_final_keyboard(review_token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نشر في القنوات الفعّالة", callback_data=f"rec:publish:{review_token}")],
        [
            InlineKeyboardButton("📢 اختيار القنوات", callback_data=f"rec:choose_channels:{review_token}"),
            InlineKeyboardButton("📝 إضافة/تعديل ملاحظات", callback_data=f"rec:add_notes:{review_token}"),
        ],
        [InlineKeyboardButton("❌ إلغاء", callback_data=f"rec:cancel:{review_token}")],
    ])


def build_channel_picker_keyboard(
    review_token: str,
    channels: Iterable[dict],
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
    if max_page > 1: nav.append(InlineKeyboardButton(f"صفحة {page}/{max_page}", callback_data="noop"))
    if page < max_page: nav.append(InlineKeyboardButton("➡️", callback_data=f"pubsel:nav:{review_token}:{page+1}"))
    if nav: rows.append(nav)

    rows.append([
        InlineKeyboardButton("🚀 نشر المحدد", callback_data=f"pubsel:confirm:{review_token}"),
        InlineKeyboardButton("⬅️ رجوع", callback_data=f"pubsel:back:{review_token}"),
    ])

    return InlineKeyboardMarkup(rows)


def build_subscription_keyboard(channel_link: Optional[str]) -> Optional[InlineKeyboardMarkup]:
    """
    Builds the keyboard with a link to the main channel for non-subscribed users.
    ✅ LOGIC FIX: Now robustly handles cases where a link might not be available.
    """
    if channel_link:
        return InlineKeyboardMarkup([[InlineKeyboardButton("➡️ الانضمام للقناة", url=channel_link)]])
    
    # Return None if no link can be generated, the handler will send the message without a button.
    return None


def build_signal_tracking_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    """Builds the interactive keyboard for a user tracking a specific signal."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔔 نبهني عند الهدف الأول", callback_data=f"track:notify_tp1:{rec_id}"),
            InlineKeyboardButton("🔔 نبهني عند وقف الخسارة", callback_data=f"track:notify_sl:{rec_id}")
        ],
        [
            InlineKeyboardButton("➕ أضف إلى محفظتي (قريباً)", callback_data=f"track:add_portfolio:{rec_id}")
        ]
    ])

# --- END OF FINAL, COMPLETE, AND UX-FIXED FILE ---