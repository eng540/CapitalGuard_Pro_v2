#--- START OF FILE: src/capitalguard/interfaces/telegram/keyboards.py ---
from __future__ import annotations
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)

# ========== لوحات المحادثة أثناء الإنشاء ==========
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

# ========== لوحات Inline للمراجعة/النشر ==========
def confirm_recommendation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نشر", callback_data="pub|yes"),
         InlineKeyboardButton("❌ إلغاء", callback_data="pub|no")]
    ])

def skip_notes_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("تخطي الملاحظات", callback_data="notes|-")]
    ])

# ========== لوحة التحكم الخاصة بالمحلّل (في الخاص فقط) ==========
def control_panel_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=f"tp|{rec_id}"),
         InlineKeyboardButton("🛡️ تعديل SL", callback_data=f"sl|{rec_id}")],
        [InlineKeyboardButton("🛑 إغلاق الآن", callback_data=f"close|{rec_id}"),
         InlineKeyboardButton("📜 السجل", callback_data=f"hist|{rec_id}")],
        [InlineKeyboardButton("SL -0.5%", callback_data=f"qa|SL|{rec_id}|-0.5"),
         InlineKeyboardButton("SL +0.5%", callback_data=f"qa|SL|{rec_id}|0.5")],
        [InlineKeyboardButton("TP1 -0.5%", callback_data=f"qa|TP|{rec_id}|-0.5"),
         InlineKeyboardButton("TP1 +0.5%", callback_data=f"qa|TP|{rec_id}|0.5")],
    ])
#--- END OF FILE --