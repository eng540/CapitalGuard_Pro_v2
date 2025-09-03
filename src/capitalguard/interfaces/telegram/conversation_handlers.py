# --- START OF FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
import logging
import uuid
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    Application, ContextTypes, ConversationHandler, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters
)
from .helpers import get_service
from .ui_texts import build_review_text_with_price
from .keyboards import (
    review_final_keyboard, asset_choice_keyboard, side_market_keyboard,
    market_choice_keyboard, order_type_keyboard
)
from .commands import main_creation_keyboard, change_method_keyboard, newrec_entry_point, settings_cmd
from .parsers import parse_quick_command, parse_text_editor

log = logging.getLogger(__name__)

# --- State Definitions & Keys ---
(CHOOSE_METHOD, QUICK_COMMAND, TEXT_EDITOR) = range(3)
(
    I_ASSET_CHOICE, I_ASSET_NEW, I_SIDE_MARKET, I_ORDER_TYPE, I_PRICES, I_NOTES, I_REVIEW
) = range(3, 10)
USER_PREFERENCE_KEY = "preferred_creation_method"
CONVERSATION_DATA_KEY = "new_rec_draft"

# --- Unified Logic (Unchanged) ---
async def show_review_card(update: Update, context: ContextTypes.DEFAULT_TYPE, is_edit: bool = False) -> int:
    message = update.message or update.callback_query.message; review_key = context.user_data.get('current_review_key')
    if review_key and review_key in context.bot_data: data = context.bot_data[review_key]
    else: data = context.user_data.get(CONVERSATION_DATA_KEY, {})
    if not data.get("asset"): await message.reply_text("Something went wrong, please start over."); return ConversationHandler.END
    price_service = get_service(context, "price_service")
    preview_price = price_service.get_preview_price(data["asset"], data.get("market", "Futures"))
    review_text = build_review_text_with_price(data, preview_price)
    if not review_key:
        review_key = str(uuid.uuid4()); context.user_data['current_review_key'] = review_key; context.bot_data[review_key] = data.copy()
    keyboard = review_final_keyboard(review_key)
    try:
        if is_edit: await message.edit_text(text=review_text, reply_markup=keyboard, parse_mode='HTML')
        else: await message.reply_html(text=review_text, reply_markup=keyboard)
    except Exception as e:
        log.warning(f"Could not edit review card, sending new one. Error: {e}")
        await message.reply_html(text=review_text, reply_markup=keyboard)
    return I_REVIEW
# ... (publish_handler, cancel_publish_handler, cancel_conv_handler etc. are assumed to be complete and correct from previous versions)

# --- Main Conversation Flow (Unchanged) ---
async def change_method_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: #...
async def method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: #...
async def quick_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: #...
async def text_editor_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: #...

# --- Updated Interactive Builder Flow ---
async def start_interactive_builder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message or update.callback_query.message
    context.user_data[CONVERSATION_DATA_KEY] = {}
    recent_assets = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "AVAXUSDT"] # Mocked data
    await message.edit_text("🚀 Interactive Builder\n\n1️⃣ Choose an asset:", reply_markup=asset_choice_keyboard(recent_assets))
    return I_ASSET_CHOICE

async def asset_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    choice = query.data.split('_')[1]
    if choice == "new":
        await query.message.edit_text("✍️ Please type the new asset symbol (e.g., ADAUSDT).")
        return I_ASSET_NEW
    context.user_data[CONVERSATION_DATA_KEY]['asset'] = choice
    market = context.user_data.get('preferred_market', 'Futures')
    context.user_data[CONVERSATION_DATA_KEY]['market'] = market
    await query.message.edit_text(f"✅ Asset: {choice}\n\n2️⃣ Select direction:", reply_markup=side_market_keyboard(market))
    return I_SIDE_MARKET

async def new_asset_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    asset = update.message.text.strip().upper()
    context.user_data[CONVERSATION_DATA_KEY]['asset'] = asset
    market = context.user_data.get('preferred_market', 'Futures')
    context.user_data[CONVERSATION_DATA_KEY]['market'] = market
    await update.message.reply_text(f"✅ Asset: {asset}\n\n2️⃣ Select direction:", reply_markup=side_market_keyboard(market))
    return I_SIDE_MARKET

async def side_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    side = query.data.split('_')[1]
    context.user_data[CONVERSATION_DATA_KEY]['side'] = side
    asset = context.user_data[CONVERSATION_DATA_KEY]['asset']
    # ✅ --- NEW STEP: Ask for order type ---
    await query.message.edit_text(
        f"✅ Asset: {asset} ({side})\n\n3️⃣ Select the entry order type:",
        reply_markup=order_type_keyboard()
    )
    return I_ORDER_TYPE

async def order_type_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the user's choice of order type and adapts the next step."""
    query = update.callback_query; await query.answer()
    order_type = query.data.split('_')[1]
    draft = context.user_data[CONVERSATION_DATA_KEY]
    draft['order_type'] = order_type
    
    if order_type == 'MARKET':
        # For market orders, we only need Stop Loss and Targets.
        await query.message.edit_text(
            f"✅ Order Type: Market\n\n"
            "4️⃣ Send STOP LOSS and TARGETS:\n`STOP TARGETS...`\n\n"
            "Example: `64000 66k 68000`"
        )
    else: # Limit or Stop Market
        await query.message.edit_text(
            f"✅ Order Type: {order_type}\n\n"
            "4️⃣ Now, send all prices:\n`ENTRY STOP TARGETS...`\n\n"
            "Example: `65000 64000 66k 68000`"
        )
    return I_PRICES

def _parse_price_string(price_str: str) -> float:
    s = price_str.strip().lower();
    if 'k' in s: return float(s.replace('k', '')) * 1000
    return float(s)

async def prices_received_interactive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles receiving price information for all order types."""
    try:
        draft = context.user_data[CONVERSATION_DATA_KEY]
        order_type = draft.get('order_type')
        parts = update.message.text.strip().replace(',', ' ').split()

        if order_type == 'MARKET':
            if len(parts) < 2: raise ValueError("At least Stop Loss and one Target are required.")
            draft["entry"] = 0  # Placeholder, will be replaced by live price on publish.
            draft["stop_loss"] = _parse_price_string(parts[0])
            draft["targets"] = [_parse_price_string(t) for t in parts[1:]]
        else: # Limit or Stop Market
            if len(parts) < 3: raise ValueError("Entry, Stop, and at least one Target are required.")
            draft["entry"] = _parse_price_string(parts[0])
            draft["stop_loss"] = _parse_price_string(parts[1])
            draft["targets"] = [_parse_price_string(t) for t in parts[2:]]
        
        # Smart validation can be added here before showing the review card
        return await show_review_card(update, context)
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Invalid price format. Please try again.")
        return I_PRICES

async def add_notes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: #...
async def notes_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: #...

# --- Registration Function ---
def register_conversation_handlers(app: Application):
    from .auth import ALLOWED_FILTER
    creation_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("newrec", newrec_entry_point, filters=ALLOWED_FILTER),
            CommandHandler("settings", settings_cmd, filters=ALLOWED_FILTER),
        ],
        states={
            CHOOSE_METHOD: [CallbackQueryHandler(method_chosen, pattern="^method_"), CallbackQueryHandler(change_method_handler, pattern="^change_method$")],
            QUICK_COMMAND: [MessageHandler(filters.COMMAND & filters.Regex(r'^\/rec'), quick_command_handler)],
            TEXT_EDITOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, text_editor_handler)],
            
            I_ASSET_CHOICE: [CallbackQueryHandler(asset_chosen, pattern="^asset_")],
            I_ASSET_NEW: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_asset_received)],
            I_SIDE_MARKET: [
                CallbackQueryHandler(side_chosen, pattern="^side_"),
                CallbackQueryHandler(change_market_menu, pattern="^change_market_menu$"),
                CallbackQueryHandler(market_chosen, pattern="^market_")
            ],
            # ✅ --- NEW STATE for Order Type ---
            I_ORDER_TYPE: [CallbackQueryHandler(order_type_chosen, pattern="^type_")],
            I_PRICES: [MessageHandler(filters.TEXT & ~filters.COMMAND, prices_received_interactive)],
            I_REVIEW: [
                CallbackQueryHandler(add_notes_handler, pattern=r"^rec:add_notes:"),
                CallbackQueryHandler(publish_handler, pattern=r"^rec:publish:"),
                CallbackQueryHandler(cancel_publish_handler, pattern=r"^rec:cancel:")
            ],
            I_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, notes_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv_handler)],
        per_message=False,
        allow_reentry=True
    )
    app.add_handler(creation_conv_handler)
# --- (The rest of the file is assumed to be complete from previous correct versions) ---
# --- END OF FILE ---