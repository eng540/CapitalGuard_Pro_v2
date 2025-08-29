# --- START OF FILE: src/capitalguard/interfaces/telegram/keyboards.py ---
from __future__ import annotations
from typing import List

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
)

# ======================
# Reply Keyboards (لمراحل الإدخال في المحادثة)
# ======================

def side_reply_keyboard() -> ReplyKeyboardMarkup:
    """
    لوحة اختيار الاتجاه: LONG / SHORT
    تُستخدم في محادثة /newrec لتحسين تجربة الإدخال.
    """
    rows: List[List[KeyboardButton]] = [
        [KeyboardButton("LONG"), KeyboardButton("SHORT")]
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True, selective=True)

def market_reply_keyboard() -> ReplyKeyboardMarkup:
    """
    لوحة اختيار السوق: Spot / Futures
    (إن كانت محادثتك تستخدم هذا الحقل).
    """
    rows: List[List[KeyboardButton]] = [
        [KeyboardButton("Spot"), KeyboardButton("Futures")]
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True, selective=True)

def cancel_reply_keyboard() -> ReplyKeyboardMarkup:
    """
    لوحة تحتوي زر إلغاء سريع للمستخدم أثناء المحادثة.
    """
    rows: List[List[KeyboardButton]] = [
        [KeyboardButton("/cancel")]
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True, selective=True)

# يمكن استخدامه لإزالة لوحة الرد بعد انتهاء المحادثة
REMOVE_REPLY_KEYBOARD = ReplyKeyboardRemove()

# ======================
# Inline Keyboards (للمراجعة/النشر والإدارة)
# ======================

def confirm_recommendation_keyboard(user_data_key: str) -> InlineKeyboardMarkup:
    """
    أزرار لتأكيد نشر التوصية أو إلغائها.
    callback_data: rec:publish:<uuid> / rec:cancel:<uuid>
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ نشر في القناة", callback_data=f"rec:publish:{user_data_key}"),
            InlineKeyboardButton("❌ إلغاء", callback_data=f"rec:cancel:{user_data_key}")
        ]
    ])

def recommendation_management_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    """
    أزرار لإدارة توصية داخل الخاص (فتح القائمة من /open).
    callback_data: rec:amend_tp:<id> / rec:amend_sl:<id> / rec:close:<id> / rec:history:<id>
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=f"rec:amend_tp:{rec_id}"),
            InlineKeyboardButton("🛡️ تعديل SL", callback_data=f"rec:amend_sl:{rec_id}"),
        ],
        [
            InlineKeyboardButton("📜 السجل", callback_data=f"rec:history:{rec_id}"),
            InlineKeyboardButton("🛑 إغلاق الآن", callback_data=f"rec:close:{rec_id}"),
        ]
    ])

def confirm_close_keyboard(rec_id: int, exit_price: float) -> InlineKeyboardMarkup:
    """
    أزرار لتأكيد أو إلغاء عملية الإغلاق.
    callback_data: rec:confirm_close:<id>:<exit_price> / rec:cancel_close:<id>
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ تأكيد الإغلاق",
                callback_data=f"rec:confirm_close:{rec_id}:{exit_price}"
            ),
            InlineKeyboardButton(
                "❌ تراجع",
                callback_data=f"rec:cancel_close:{rec_id}"
            )
        ]
    ])

def channel_card_keyboard(rec_id: int, *, is_open: bool) -> InlineKeyboardMarkup:
    """
    أزرار تُرفق مع البطاقة المنشورة في القناة العامة.
    - عند OPEN: تعديل SL/الأهداف، السجل، إغلاق الآن.
    - عند CLOSED: عرض السجل فقط.
    """
    if is_open:
        rows = [
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