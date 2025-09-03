# --- START OF FILE: src/capitalguard/interfaces/telegram/keyboards.py ---
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from capitalguard.config import settings
from typing import List

# --- (Keyboards for publishing and public channel remain the same) ---
def confirm_recommendation_keyboard(user_data_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نشر في القناة", callback_data=f"rec:publish:{user_data_key}"),
         InlineKeyboardButton("❌ إلغاء", callback_data=f"rec:cancel:{user_data_key}")]
    ])
def public_channel_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "YourBotName")
    follow_url = f"https://t.me/{bot_username}?start=follow_{rec_id}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث البيانات الحية", callback_data=f"rec:update_public:{rec_id}"),
         InlineKeyboardButton("🤖 الانضمام والمتابعة", url=follow_url)]
    ])
def analyst_control_panel_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث السعر", callback_data=f"rec:update_private:{rec_id}"),
         InlineKeyboardButton("✏️ تعديل", callback_data=f"rec:edit_menu:{rec_id}")],
        [InlineKeyboardButton("🛡️ نقل للـ BE", callback_data=f"rec:move_be:{rec_id}"),
         InlineKeyboardButton("💰 إغلاق 50% (ملاحظة)", callback_data=f"rec:close_partial:{rec_id}")],
        [InlineKeyboardButton("❌ إغلاق كلي", callback_data=f"rec:close_start:{rec_id}")]
    ])
def analyst_edit_menu_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 تعديل الوقف", callback_data=f"rec:edit_sl:{rec_id}"),
         InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=f"rec:edit_tp:{rec_id}")],
        [InlineKeyboardButton("⬅️ العودة للوحة التحكم", callback_data=f"rec:back_to_main:{rec_id}")]
    ])
def confirm_close_keyboard(rec_id: int, exit_price: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد الإغلاق", callback_data=f"rec:confirm_close:{rec_id}:{exit_price}"),
         InlineKeyboardButton("❌ تراجع", callback_data=f"rec:cancel_close:{rec_id}")]
    ])

# ✅ --- NEW KEYBOARDS FOR THE INTERACTIVE BUILDER ---
def asset_choice_keyboard(recent_assets: List[str]) -> InlineKeyboardMarkup:
    """Creates a keyboard with buttons for recent assets."""
    buttons = [InlineKeyboardButton(asset, callback_data=f"asset_{asset}") for asset in recent_assets]
    # Arrange buttons in rows of 3
    keyboard_layout = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    keyboard_layout.append([InlineKeyboardButton("✍️ اكتب أصلاً جديدًا", callback_data="asset_new")])
    return InlineKeyboardMarkup(keyboard_layout)

def side_market_keyboard(current_market: str = "Futures") -> InlineKeyboardMarkup:
    """Creates the combined keyboard for side and market selection."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"LONG / {current_market}", callback_data=f"side_LONG"),
            InlineKeyboardButton(f"SHORT / {current_market}", callback_data=f"side_SHORT")
        ],
        [
            InlineKeyboardButton(f"🔄 تغيير السوق (الحالي: {current_market})", callback_data="change_market_menu")
        ]
    ])

def market_choice_keyboard() -> InlineKeyboardMarkup:
    """Shows the market choices."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Futures", callback_data="market_Futures"),
            InlineKeyboardButton("Spot", callback_data="market_Spot")
        ],
        [InlineKeyboardButton("⬅️ عودة", callback_data="market_back")]
    ])

def review_final_keyboard(review_key: str) -> InlineKeyboardMarkup:
    """Final review keyboard with an option to add notes."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ نشر في القناة", callback_data=f"rec:publish:{review_key}"),
            InlineKeyboardButton("📝 إضافة/تعديل ملاحظات", callback_data=f"rec:add_notes:{review_key}")
        ],
        [
            InlineKeyboardButton("❌ إلغاء", callback_data=f"rec:cancel:{review_key}")
        ]
    ])
# --- END OF FILE ---