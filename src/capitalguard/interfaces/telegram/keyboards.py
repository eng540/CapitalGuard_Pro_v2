# --- START OF FILE: src/capitalguard/interfaces/telegram/keyboards.py ---
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from capitalguard.config import settings
from capitalguard.domain.entities import Recommendation, RecommendationStatus
from capitalguard.application.services.price_service import PriceService
from capitalguard.interfaces.telegram.ui_texts import _pct
from typing import List
import math

# ✅ NEW: Add the conversation keyboards here to break the circular import.

def main_creation_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for choosing the recommendation creation method."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 المنشئ التفاعلي", callback_data="method_interactive")],
        [InlineKeyboardButton("⚡️ الأمر السريع", callback_data="method_quick")],
        [InlineKeyboardButton("📋 المحرر النصي", callback_data="method_editor")],
    ])

def change_method_keyboard() -> InlineKeyboardMarkup:
    """A simple keyboard to allow changing the input method."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تغيير طريقة الإدخال", callback_data="change_method")]])


# --- Existing keyboard functions remain unchanged ---

ITEMS_PER_PAGE = 8

def build_open_recs_keyboard(items: List[Recommendation], current_page: int, price_service: PriceService) -> InlineKeyboardMarkup:
    # ... (function content is unchanged)
    keyboard = []
    total_items = len(items)
    total_pages = math.ceil(total_items / ITEMS_PER_PAGE)
    start_index = (current_page - 1) * ITEMS_PER_PAGE
    end_index = start_index + ITEMS_PER_PAGE
    paginated_items = items[start_index:end_index]
    for rec in paginated_items:
        button_text = ""
        if rec.status == RecommendationStatus.PENDING:
            status_icon = "⏳"
            button_text = f"{status_icon} #{rec.id} - {rec.asset.value} ({rec.side.value}) | معلقة"
        elif rec.status == RecommendationStatus.ACTIVE:
            if rec.stop_loss.value == rec.entry.value:
                status_icon = "🛡️"
                button_text = f"{status_icon} #{rec.id} - {rec.asset.value} ({rec.side.value}) | BE"
            else:
                live_price = price_service.get_cached_price(rec.asset.value, rec.market)
                if live_price:
                    pnl = _pct(rec.entry.value, live_price, rec.side.value)
                    status_icon = "🟢" if pnl >= 0 else "🔴"
                    button_text = f"{status_icon} #{rec.id} - {rec.asset.value} ({rec.side.value}) | PnL: {pnl:+.2f}%"
                else:
                    status_icon = "▶️"
                    button_text = f"{status_icon} #{rec.id} - {rec.asset.value} ({rec.side.value}) | نشطة"
        callback_data = f"rec:show_panel:{rec.id}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    nav_buttons = []
    if current_page > 1:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"open_nav:page:{current_page - 1}"))
    if total_pages > 1:
        nav_buttons.append(InlineKeyboardButton(f"صفحة {current_page}/{total_pages}", callback_data="noop"))
    if current_page < total_pages:
        nav_buttons.append(InlineKeyboardButton("التالي ➡️", callback_data=f"open_nav:page:{current_page + 1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)
    return InlineKeyboardMarkup(keyboard)

def public_channel_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث البيانات الحية", callback_data=f"rec:update_public:{rec_id}")]
    ])

def analyst_control_panel_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث السعر", callback_data=f"rec:update_private:{rec_id}"), InlineKeyboardButton("✏️ تعديل", callback_data=f"rec:edit_menu:{rec_id}")],
        [InlineKeyboardButton("🛡️ نقل للـ BE", callback_data=f"rec:move_be:{rec_id}"), InlineKeyboardButton("💰 إغلاق 50% (ملاحظة)", callback_data=f"rec:close_partial:{rec_id}")],
        [InlineKeyboardButton("❌ إغلاق كلي", callback_data=f"rec:close_start:{rec_id}")],
        [InlineKeyboardButton("⬅️ العودة لقائمة التوصيات", callback_data=f"open_nav:page:1")]
    ])

def analyst_edit_menu_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 تعديل الوقف", callback_data=f"rec:edit_sl:{rec_id}"), InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=f"rec:edit_tp:{rec_id}")],
        [InlineKeyboardButton("⬅️ العودة للوحة التحكم", callback_data=f"rec:back_to_main:{rec_id}")]
    ])

def confirm_close_keyboard(rec_id: int, exit_price: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد الإغلاق", callback_data=f"rec:confirm_close:{rec_id}:{exit_price}"), InlineKeyboardButton("❌ تراجع", callback_data=f"rec:cancel_close:{rec_id}")]
    ])

def asset_choice_keyboard(recent_assets: List[str]) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(asset, callback_data=f"asset_{asset}") for asset in recent_assets]
    keyboard_layout = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    keyboard_layout.append([InlineKeyboardButton("✍️ اكتب أصلاً جديدًا", callback_data="asset_new")])
    return InlineKeyboardMarkup(keyboard_layout)

def side_market_keyboard(current_market: str = "Futures") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"LONG / {current_market}", callback_data=f"side_LONG"), InlineKeyboardButton(f"SHORT / {current_market}", callback_data=f"side_SHORT")],
        [InlineKeyboardButton(f"🔄 تغيير السوق (الحالي: {current_market})", callback_data="change_market_menu")]
    ])

def market_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Futures", callback_data="market_Futures"), InlineKeyboardButton("Spot", callback_data="market_Spot")],
        [InlineKeyboardButton("⬅️ عودة", callback_data="market_back")]
    ])

def order_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Market (دخول فوري بالسعر الحالي)", callback_data="type_MARKET")],
        [InlineKeyboardButton("Limit (انتظار سعر أفضل للدخول)", callback_data="type_LIMIT")],
        [InlineKeyboardButton("Stop Market (دخول بعد اختراق سعر معين)", callback_data="type_STOP_MARKET")],
    ])

def review_final_keyboard(review_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نشر في القناة", callback_data=f"rec:publish:{review_key}"), InlineKeyboardButton("📝 إضافة/تعديل ملاحظات", callback_data=f"rec:add_notes:{review_key}")],
        [InlineKeyboardButton("❌ إلغاء", callback_data=f"rec:cancel:{review_key}")]
    ])
# --- END OF FILE ---