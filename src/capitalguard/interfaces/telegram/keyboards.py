# --- START OF FILE: src/capitalguard/interfaces/telegram/keyboards.py ---
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from capitalguard.config import settings
from typing import List

# ... (confirm_recommendation, public_channel, analyst_control_panel, etc. are unchanged)

# --- Keyboards for the Interactive Builder ---
def asset_choice_keyboard(recent_assets: List[str]) -> InlineKeyboardMarkup: #... (unchanged)
def side_market_keyboard(current_market: str = "Futures") -> InlineKeyboardMarkup: #... (unchanged)
def market_choice_keyboard() -> InlineKeyboardMarkup: #... (unchanged)

# ✅ --- NEW: Keyboard for choosing the order type ---
def order_type_keyboard() -> InlineKeyboardMarkup:
    """Creates a keyboard to select the order entry type."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Market (دخول فوري)", callback_data="type_MARKET")],
        [InlineKeyboardButton("Limit (انتظار سعر أفضل)", callback_data="type_LIMIT")],
        [InlineKeyboardButton("Stop Market (دخول عند الاختراق)", callback_data="type_STOP_MARKET")],
    ])

def review_final_keyboard(review_key: str) -> InlineKeyboardMarkup:
    # ... (unchanged)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ نشر في القناة", callback_data=f"rec:publish:{review_key}"),
            InlineKeyboardButton("📝 إضافة/تعديل ملاحظات", callback_data=f"rec:add_notes:{review_key}")
        ],
        [InlineKeyboardButton("❌ إلغاء", callback_data=f"rec:cancel:{review_key}")]
    ])
# --- END OF FILE ---