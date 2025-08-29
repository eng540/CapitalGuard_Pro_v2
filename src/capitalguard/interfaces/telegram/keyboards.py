# --- START OF FILE: src/capitalguard/interfaces/telegram/keyboards.py ---
from __future__ import annotations
from typing import List
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove

# لوحة تحكم خاصة (DM) — لا تُستخدم في القناة
def control_panel_keyboard(rec_id: int, *, is_open: bool) -> InlineKeyboardMarkup:
    if is_open:
        rows = [
            [
                InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=f"rec:amend_tp:{rec_id}"),
                InlineKeyboardButton("🛡️ تعديل SL",     callback_data=f"rec:amend_sl:{rec_id}"),
            ],
            [
                InlineKeyboardButton("📜 السجل", callback_data=f"rec:history:{rec_id}"),
                InlineKeyboardButton("🛑 إغلاق الآن", callback_data=f"rec:close:{rec_id}"),
            ],
        ]
    else:
        rows = [[InlineKeyboardButton("📜 السجل", callback_data=f"rec:history:{rec_id}")]]
    return InlineKeyboardMarkup(rows)

# لوحات إدخال مبسطة
def side_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["LONG", "SHORT"]], resize_keyboard=True, one_time_keyboard=True, input_field_placeholder="اختر الاتجاه")

def market_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["Spot", "Futures"]], resize_keyboard=True, one_time_keyboard=True, input_field_placeholder="اختر النوع")

def yes_no_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["نشر في القناة ✅", "إلغاء ❌"]], resize_keyboard=True, one_time_keyboard=True)

def remove_reply_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()
# --- END OF FILE ---