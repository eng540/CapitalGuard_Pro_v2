from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def close_buttons(rec_id: int) -> InlineKeyboardMarkup:
    cb = f"cg:close:{rec_id}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("إغلاق هذه التوصية", callback_data=cb)]
    ])

def confirm_close_buttons(rec_id: int, exit_price: float) -> InlineKeyboardMarkup:
    yes = f"cg:confirmclose:{rec_id}:{exit_price}"
    no  = f"cg:cancelclose:{rec_id}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد الإغلاق", callback_data=yes)],
        [InlineKeyboardButton("❌ إلغاء", callback_data=no)]
    ])

def list_nav_buttons(page: int, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    rows = []
    nav = []
    if has_prev:
        nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"cg:list:page:{page-1}"))
    if has_next:
        nav.append(InlineKeyboardButton("➡️ التالي", callback_data=f"cg:list:page:{page+1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows) if rows else No


def close_buttons(rec_id: int) -> InlineKeyboardMarkup:
    # namespace callback data to avoid collisions with other bots
    cb = f"cg:close:{rec_id}"
    return InlineKeyboardMarkup([[InlineKeyboardButton("إغلاق هذه التوصية", callback_data=cb)]])