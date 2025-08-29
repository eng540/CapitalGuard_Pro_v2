# --- START OF FILE: src/capitalguard/interfaces/telegram/keyboards.py ---
from __future__ import annotations
from typing import Iterable
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# ====== Inline Keyboards (تحكّم كامل داخل البوت) ======

def side_inline_keyboard() -> InlineKeyboardMarkup:
    # newrec:side:<LONG|SHORT>
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("📈 LONG",  callback_data="newrec:side:LONG"),
            InlineKeyboardButton("📉 SHORT", callback_data="newrec:side:SHORT"),
        ]]
    )

def market_inline_keyboard() -> InlineKeyboardMarkup:
    # newrec:market:<Spot|Futures>
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Spot",    callback_data="newrec:market:Spot"),
            InlineKeyboardButton("Futures", callback_data="newrec:market:Futures"),
        ]]
    )

def notes_inline_keyboard() -> InlineKeyboardMarkup:
    # newrec:notes:skip  (يُستخدم لتجاوز الملاحظات)
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⏭️ تخطي الملاحظة", callback_data="newrec:notes:skip")]]
    )

def recommendation_management_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    """
    أزرار إدارة التوصية داخل البوت فقط (لا تُنشر للقناة).
    """
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 إغلاق الآن", callback_data=f"rec:close:{rec_id}")],
        [
            InlineKeyboardButton("🛡️ تعديل SL",  callback_data=f"rec:amend_sl:{rec_id}"),
            InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=f"rec:amend_tp:{rec_id}"),
        ],
        [InlineKeyboardButton("📜 السجل", callback_data=f"rec:history:{rec_id}")],
    ])

def confirm_close_keyboard(rec_id: int, exit_price: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ تأكيد الإغلاق", callback_data=f"rec:confirm_close:{rec_id}:{exit_price}"),
            InlineKeyboardButton("❌ تراجع",         callback_data=f"rec:cancel_close:{rec_id}"),
        ]]
    )
# --- END OF FILE ---