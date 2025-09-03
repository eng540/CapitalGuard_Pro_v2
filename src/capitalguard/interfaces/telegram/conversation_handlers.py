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
    confirm_recommendation_keyboard, asset_choice_keyboard, side_market_keyboard,
    market_choice_keyboard, review_final_keyboard
)
from .commands import main_creation_keyboard, change_method_keyboard, newrec_entry_point, settings_cmd
from .parsers import parse_quick_command, parse_text_editor

log = logging.getLogger(__name__)

# --- State Definitions & Keys ---
(CHOOSE_METHOD, QUICK_COMMAND, TEXT_EDITOR) = range(3)
(
    I_ASSET_CHOICE, I_ASSET_NEW, I_SIDE_MARKET, I_PRICES, I_NOTES, I_REVIEW
) = range(3, 9) # States for the FULL interactive flow
USER_PREFERENCE_KEY = "preferred_creation_method"
CONVERSATION_DATA_KEY = "new_rec_draft"

# --- (Unified Post-Parsing Logic and Final Handlers remain mostly the same) ---
async def process_parsed_data(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict) -> int:
    price_service = get_service(context, "price_service")
    preview_price = price_service.get_preview_price(data["asset"], data.get("market", "Futures"))
    review_text = build_review_text_with_price(data, preview_price)
    review_key = str(uuid.uuid4()); context.bot_data[review_key] = data.copy()
    message = update.message or update.callback_query.message
    await message.reply_html(text=review_text, reply_markup=review_final_keyboard(review_key))
    return ConversationHandler.END
# ... (publish_handler, cancel_publish_handler etc. are fine)

# --- NEW FULLY INTERACTIVE BUILDER FLOW ---

async def start_interactive_builder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the new interactive builder experience."""
    query = update.callback_query; await query.answer()
    context.user_data[CONVERSATION_DATA_KEY] = {}
    
    # Fetch recent assets for this user (mocked for now, needs DB query)
    trade_service = get_service(context, "trade_service")
    # This logic needs to be implemented in the repository/service layer
    # recent_assets = trade_service.get_recent_assets_for_user(update.effective_user.id)
    recent_assets = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "AVAXUSDT"] # Mock data
    
    await query.message.edit_text(
        "🚀 Interactive Builder\n\n1️⃣ Choose a recent asset, or add a new one:",
        reply_markup=asset_choice_keyboard(recent_assets)
    )
    return I_ASSET_CHOICE

async def asset_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    choice = query.data.split('_')[1]
    
    if choice == "new":
        await query.message.edit_text("✍️ Please type the new asset symbol (e.g., ADAUSDT).")
        return I_ASSET_NEW

    context.user_data[CONVERSATION_DATA_KEY]['asset'] = choice
    # Get preferred market
    market = context.user_data.get('preferred_market', 'Futures')
    context.user_data[CONVERSATION_DATA_KEY]['market'] = market
    await query.message.edit_text(
        f"✅ Asset: {choice}\n\n2️⃣ Select the trade direction and market:",
        reply_markup=side_market_keyboard(market)
    )
    return I_SIDE_MARKET

async def new_asset_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    asset = update.message.text.strip().upper()
    context.user_data[CONVERSATION_DATA_KEY]['asset'] = asset
    market = context.user_data.get('preferred_market', 'Futures')
    context.user_data[CONVERSATION_DATA_KEY]['market'] = market
    await update.message.reply_text(
        f"✅ Asset: {asset}\n\n2️⃣ Select the trade direction and market:",
        reply_markup=side_market_keyboard(market)
    )
    return I_SIDE_MARKET

async def side_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    side = query.data.split('_')[1]
    context.user_data[CONVERSATION_DATA_KEY]['side'] = side
    asset = context.user_data[CONVERSATION_DATA_KEY]['asset']
    await query.message.edit_text(
        f"✅ Asset: {asset} ({side})\n\n"
        "3️⃣ Now, send all prices in a single message:\n`ENTRY STOP TARGETS...`\n\n"
        "Example: `65000 64000 66k 68000`"
    )
    return I_PRICES

async def change_market_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await query.message.edit_reply_markup(reply_markup=market_choice_keyboard())
    return I_SIDE_MARKET # Stay in the same state, just change the keyboard

async def market_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    choice = query.data
    if choice == "market_back":
        market = context.user_data.get('preferred_market', 'Futures')
    else:
        market = choice.split('_')[1]
        context.user_data['preferred_market'] = market # Remember preference
    
    context.user_data[CONVERSATION_DATA_KEY]['market'] = market
    await query.message.edit_reply_markup(reply_markup=side_market_keyboard(market))
    return I_SIDE_MARKET

async def prices_received_interactive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        parts = update.message.text.strip().replace(',', ' ').split()
        if len(parts) < 3: raise ValueError("At least Entry, Stop, and one Target are required.")
        # ... (same price parsing logic as before)
        entry_price = float(parts[0]); stop_loss = float(parts[1]); targets = []
        for t in parts[2:]:
            t = t.strip().lower()
            if 'k' in t: targets.append(float(t.replace('k', '')) * 1000)
            else: targets.append(float(t))
        
        draft = context.user_data[CONVERSATION_DATA_KEY]
        draft["entry"] = entry_price
        draft["stop_loss"] = stop_loss
        draft["targets"] = targets

        # Skip notes for now, go straight to review
        return await process_parsed_data(update, context, draft)
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Invalid price format. Please try again:\n`ENTRY STOP TARGETS...`")
        return I_PRICES

async def add_notes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    review_key = query.data.split(':')[2]
    context.user_data['review_key_for_notes'] = review_key
    await query.message.edit_text(f"{query.message.text}\n\n✍️ Please send your notes now.")
    return I_NOTES

async def notes_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    notes = update.message.text.strip()
    review_key = context.user_data.pop('review_key_for_notes', None)
    if review_key and review_key in context.bot_data:
        draft = context.bot_data[review_key]
        draft['notes'] = notes if notes.lower() not in ['skip', 'none'] else None
        # Go back to the review
        return await process_parsed_data(update, context, draft)
    
    await update.message.reply_text("Something went wrong. Please start over.")
    return ConversationHandler.END

# --- (The rest of the file: choosing method, quick command, text editor, publishing, etc.) ---
# This part is now the setup for the ConversationHandler
def register_conversation_handlers(app: Application):
    from .auth import ALLOWED_FILTER
    # ... (other handlers like quick_command_handler, text_editor_handler are defined here as before)
    # ... (publish_handler, cancel_publish_handler etc. are defined here as before)

    creation_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("newrec", newrec_entry_point, filters=ALLOWED_FILTER),
            CommandHandler("settings", settings_cmd, filters=ALLOWED_FILTER),
            CallbackQueryHandler(change_method_handler, pattern="^change_method$"),
        ],
        states={
            # Main Menu
            CHOOSE_METHOD: [CallbackQueryHandler(method_chosen, pattern="^method_")],
            # Quick/Editor Flows (lead to parsing and end)
            QUICK_COMMAND: [MessageHandler(filters.COMMAND & filters.Regex(r'^\/rec'), quick_command_handler)],
            TEXT_EDITOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, text_editor_handler)],
            
            # New Full Interactive Flow
            I_ASSET_CHOICE: [CallbackQueryHandler(asset_chosen, pattern="^asset_")],
            I_ASSET_NEW: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_asset_received)],
            I_SIDE_MARKET: [
                CallbackQueryHandler(side_chosen, pattern="^side_"),
                CallbackQueryHandler(change_market_menu, pattern="^change_market_menu$"),
                CallbackQueryHandler(market_chosen, pattern="^market_")
            ],
            I_PRICES: [MessageHandler(filters.TEXT & ~filters.COMMAND, prices_received_interactive)],
            I_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, notes_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv_handler)],
        allow_reentry=True
    )
    app.add_handler(creation_conv_handler)
    app.add_handler(CallbackQueryHandler(publish_handler, pattern=r"^rec:publish:"))
    app.add_handler(CallbackQueryHandler(cancel_publish_handler, pattern=r"^rec:cancel:"))
    app.add_handler(CallbackQueryHandler(add_notes_handler, pattern=r"^rec:add_notes:")) # New handler for adding notes
# --- END OF FILE ---