# --- START OF FILE: src/capitalguard/interfaces/telegram/keyboards.py ---
from __future__ import annotations
from typing import Optional
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

def bot_control_keyboard(rec_id: int, *, is_open: bool) -> InlineKeyboardMarkup:
    """
    لوحة التحكّم داخل محادثة البوت فقط.
    """
    rows = []
    if is_open:
        rows.append([
            InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=f"rec:amend_tp:{rec_id}"),
            InlineKeyboardButton("🛡️ تعديل SL", callback_data=f"rec:amend_sl:{rec_id}"),
        ])
        rows.append([
            InlineKeyboardButton("إغلاق الآن ⛔", callback_data=f"rec:close:{rec_id}"),
        ])
    rows.append([InlineKeyboardButton("السجل 🧾", callback_data=f"rec:history:{rec_id}")])
    return InlineKeyboardMarkup(rows)
# --- END OF FILE ---