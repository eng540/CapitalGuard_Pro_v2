# --- START OF FILE: src/capitalguard/interfaces/telegram/keyboards.py ---
from __future__ import annotations
from typing import List
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove

# ------------- Inline keyboards (داخل البوت) -------------

def recommendation_management_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    """
    أزرار لإدارة توصية مفتوحة داخل البوت فقط.
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛑 إغلاق الآن", callback_data=f"rec:close:{rec_id}"),
        ],
        [
            InlineKeyboardButton("🛡️ تعديل SL", callback_data=f"rec:amend_sl:{rec_id}"),
            InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=f"rec:amend_tp:{rec_id}"),
        ],
        [
            InlineKeyboardButton("📜 السجل", callback_data=f"rec:history:{rec_id}")
        ]
    ])


def confirm_close_keyboard(rec_id: int, exit_price: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تأكيد الإغلاق", callback_data=f"rec:confirm_close:{rec_id}:{exit_price}"),
            InlineKeyboardButton("❌ تراجع", callback_data=f"rec:cancel_close:{rec_id}"),
        ]
    ])


# ------------- Reply keyboards (تسهيل الإدخال) -------------

def side_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["LONG", "SHORT"]],
        resize_keyboard=True,
        one_time_keyboard=True,
        selective=True,
        input_field_placeholder="اختر الاتجاه",
    )

def market_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["Spot", "Futures"]],
        resize_keyboard=True,
        one_time_keyboard=True,
        selective=True,
        input_field_placeholder="اختر نوع السوق",
    )

def remove_reply_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()
# --- END OF FILE ---