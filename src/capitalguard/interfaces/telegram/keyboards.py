# --- START OF FILE: src/capitalguard/interfaces/telegram/keyboards.py ---
from __future__ import annotations
from typing import List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove


def channel_card_keyboard(rec_id: int, *, is_open: bool) -> InlineKeyboardMarkup:
    if is_open:
        rows: List[List[InlineKeyboardButton]] = [
            [
                InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=f"rec:amend_tp:{rec_id}"),
                InlineKeyboardButton("🛡️ تعديل SL", callback_data=f"rec:amend_sl:{rec_id}"),
            ],
            [
                InlineKeyboardButton("📜 السجل", callback_data=f"rec:history:{rec_id}"),
                InlineKeyboardButton("🛑 إغلاق الآن", callback_data=f"rec:close:{rec_id}"),
            ],
        ]
    else:
        rows = [[InlineKeyboardButton("📜 السجل", callback_data=f"rec:history:{rec_id}")]]
    return InlineKeyboardMarkup(rows)


def recommendation_management_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎯 تحديث الأهداف", callback_data=f"rec:update_tp:{rec_id}"),
            InlineKeyboardButton("🛡️ تعديل SL", callback_data=f"rec:update_sl:{rec_id}"),
        ],
        [
            InlineKeyboardButton("📜 السجل", callback_data=f"rec:history:{rec_id}"),
            InlineKeyboardButton("🛑 إغلاق الآن", callback_data=f"rec:close:{rec_id}"),
        ]
    ])


def confirm_close_keyboard(rec_id: int, exit_price: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تأكيد الإغلاق", callback_data=f"rec:confirm_close:{rec_id}:{exit_price}"),
            InlineKeyboardButton("❌ تراجع", callback_data=f"rec:cancel_close:{rec_id}"),
        ]
    ])


def side_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["LONG", "SHORT"]], resize_keyboard=True, one_time_keyboard=True)


def market_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["Spot", "Futures"]], resize_keyboard=True, one_time_keyboard=True)


def remove_reply_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def confirm_recommendation_keyboard(user_data_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ نشر في القناة", callback_data=f"rec:publish:{user_data_key}"),
            InlineKeyboardButton("❌ إلغاء", callback_data=f"rec:cancel:{user_data_key}")
        ]
    ])
# --- END OF FILE ---