# --- START OF FILE: src/capitalguard/interfaces/telegram/keyboards.py ---
from __future__ import annotations
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

# ——— أزرار المحادثة (اختيار اتجاه ونوع السوق) ———
def choose_side_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("LONG"), KeyboardButton("SHORT")]],
        resize_keyboard=True, one_time_keyboard=True
    )

def choose_market_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("Spot"), KeyboardButton("Futures")]],
        resize_keyboard=True, one_time_keyboard=True
    )

def remove_reply_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()

# ——— أزرار تأكيد النشر أو الإلغاء (داخل البوت) ———
def confirm_recommendation_keyboard(user_data_key: str) -> InlineKeyboardMarkup:
    # callback_data: rec:publish:<key> / rec:cancel:<key>
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ نشر في القناة", callback_data=f"rec:publish:{user_data_key}"),
            InlineKeyboardButton("❌ إلغاء", callback_data=f"rec:cancel:{user_data_key}"),
        ]]
    )

# ——— لوحة تحكم توصية داخل البوت فقط ———
def control_panel_keyboard(rec_id: int, is_open: bool) -> InlineKeyboardMarkup:
    if is_open:
        rows = [
            [
                InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=f"rec:amend_tp:{rec_id}"),
                InlineKeyboardButton("🛡️ تعديل SL", callback_data=f"rec:amend_sl:{rec_id}"),
            ],
            [
                InlineKeyboardButton("📜 السجل", callback_data=f"rec:history:{rec_id}"),
                InlineKeyboardButton("🛑 إغلاق الآن", callback_data=f"rec:close:{rec_id}"),
            ]
        ]
    else:
        rows = [[InlineKeyboardButton("📜 السجل", callback_data=f"rec:history:{rec_id}")]]
    return InlineKeyboardMarkup(rows)

def close_confirmation_keyboard(rec_id: int, exit_price: float) -> InlineKeyboardMarkup:
    # callback_data: rec:confirm_close:<id>:<price> / rec:cancel_close:<id>
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تأكيد الإغلاق", callback_data=f"rec:confirm_close:{rec_id}:{exit_price}"),
        InlineKeyboardButton("❌ تراجع", callback_data=f"rec:cancel_close:{rec_id}"),
    ]])
# --- END OF FILE ---