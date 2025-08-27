from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def close_buttons(rec_id: int) -> InlineKeyboardMarkup:
    # namespace callback data to avoid collisions with other bots
    cb = f"cg:close:{rec_id}"
    return InlineKeyboardMarkup([[InlineKeyboardButton("إغلاق هذه التوصية", callback_data=cb)]])