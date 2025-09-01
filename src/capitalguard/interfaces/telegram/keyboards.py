#--- START OF FILE: src/capitalguard/interfaces/telegram/keyboards.py ---
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def confirm_recommendation_keyboard(user_data_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ نشر في القناة", callback_data=f"rec:publish:{user_data_key}"),
            InlineKeyboardButton("❌ إلغاء", callback_data=f"rec:cancel:{user_data_key}")
        ]
    ])

def recommendation_management_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛑 إغلاق الآن", callback_data=f"rec:close:{rec_id}")
        ]
    ])

def confirm_close_keyboard(rec_id: int, exit_price: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تأكيد الإغلاق", callback_data=f"rec:confirm_close:{rec_id}:{exit_price}"),
            InlineKeyboardButton("❌ تراجع", callback_data=f"rec:cancel_close:{rec_id}")
        ]
    ])
#--- END OF FILE ---