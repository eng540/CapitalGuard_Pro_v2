# --- START OF FILE: src/capitalguard/interfaces/telegram/keyboards.py ---
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def confirm_recommendation_keyboard(user_data_key: str) -> InlineKeyboardMarkup:
    """
    أزرار لتأكيد نشر التوصية أو إلغائها.
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ نشر في القناة", callback_data=f"rec:publish:{user_data_key}"),
            InlineKeyboardButton("❌ إلغاء", callback_data=f"rec:cancel:{user_data_key}")
        ]
    ])

def recommendation_management_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    """
    أزرار لإدارة توصية مفتوحة (تحديث الأهداف، إغلاق).
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎯 تحديث الأهداف", callback_data=f"rec:update_tp:{rec_id}"),
            InlineKeyboardButton("🛑 إغلاق الآن", callback_data=f"rec:close:{rec_id}")
        ]
    ])

def confirm_close_keyboard(rec_id: int, exit_price: float) -> InlineKeyboardMarkup:
    """
    أزرار لتأكيد أو إلغاء عملية الإغلاق.
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تأكيد الإغلاق", callback_data=f"rec:confirm_close:{rec_id}:{exit_price}"),
            InlineKeyboardButton("❌ تراجع", callback_data=f"rec:cancel_close:{rec_id}")
        ]
    ])
# --- END OF FILE ---