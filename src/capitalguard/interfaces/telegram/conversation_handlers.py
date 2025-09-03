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
    market_choice_keyboard
)
from .commands import main_creation_keyboard, change_method_keyboard, newrec_entry_point, settings_cmd
from .parsers import parse_quick_command, parse_text_editor

log = logging.getLogger(__name__)

# --- State Definitions & Keys ---
(CHOOSE_METHOD, QUICK_COMMAND, TEXT_EDITOR) = range(3)
(
    I_ASSET_CHOICE, I_ASSET_NEW, I_SIDE_MARKET, I_PRICES, I_NOTES, I_REVIEW
) = range(3, 9)
USER_PREFERENCE_KEY = "preferred_creation_method"
CONVERSATION_DATA_KEY = "new_rec_draft"

# --- Unified Post-Parsing & Final Handlers ---
async def process_parsed_data(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict) -> int:
    price_service = get_service(context, "price_service")
    preview_price = price_service.get_preview_price(data["asset"], data.get("market", "Futures"))
    review_text = build_review_text_with_price(data, preview_price)
    review_key = str(uuid.uuid4()); context.bot_data[review_key] = data.copy()
    message = update.message or update.callback_query.message
    await message.reply_html(text=review_text, reply_markup=review_final_keyboard(review_key))
    context.user_data.pop(CONVERSATION_DATA_KEY, None)
    return ConversationHandler.END

# ... (publish_handler, cancel_publish_handler, cancel_conv_handler etc. are unchanged)
async def publish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer("Publishing...")
    review_key = query.data.split(":")[2]; draft = context.bot_data.get(review_key)
    if not draft: await query.edit_message_text("❌ Error: Review data not found."); return
    trade_service = get_service(context, "trade_service")
    try:
        entry_val = draft["entry"]
        if isinstance(entry_val, list):
            entry_price = entry_val[0]
            if "notes" not in draft or draft["notes"] is None: draft["notes"] = ""
            draft["notes"] += f"\nEntry Zone: {entry_val[0]}-{entry_val[-1]}"
        else: entry_price = entry_val
        rec = trade_service.create_and_publish_recommendation(
            asset=draft["asset"], side=draft["side"], market=draft.get("market", "Futures"),
            entry=entry_price, stop_loss=draft["stop_loss"], targets=draft["targets"],
            notes=draft.get("notes"), user_id=str(query.from_user.id)
        ); await query.edit_message_text(f"✅ Recommendation #{rec.id} published successfully!")
    except Exception as e:
        log.exception("Failed to publish recommendation."); await query.edit_message_text(f"❌ Publication failed: {e}")
    finally: context.bot_data.pop(review_key, None)
async def cancel_publish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    review_key = query.data.split(":")[2]; context.bot_data.pop(review_key, None)
    await query.edit_message_text("Publication cancelled.")
async def cancel_conv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(CONVERSATION_DATA_KEY, None)
    await update.message.reply_text("Operation cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# --- Main Conversation Flow Handlers ---
async def change_method_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await query.message.edit_text("⚙️ Choose your preferred method:", reply_markup=main_creation_keyboard())
    return CHOOSE_METHOD
async def method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    choice = query.data.split('_')[1]; context.user_data[USER_PREFERENCE_KEY] = choice
    if choice == "interactive": return await start_interactive_builder(update, context)
    elif choice == "quick": await query.message.edit_text("⚡️ Quick Command mode.\n\nSend `/rec` command.", reply_markup=change_method_keyboard()); return QUICK_COMMAND
    elif choice == "editor": await query.message.edit_text("📋 Text Editor mode.\n\nPaste recommendation text.", reply_markup=change_method_keyboard()); return TEXT_EDITOR
    return ConversationHandler.END
async def quick_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = parse_quick_command(update.message.text)
    if not data: await update.message.reply_text("❌ Invalid format. Please try again."); return QUICK_COMMAND
    return await process_parsed_data(update, context, data)
async def text_editor_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = parse_text_editor(update.message.text)
    if not data: await update.message.reply_text("❌ Could not parse text. Ensure required fields are present."); return TEXT_EDITOR
    return await process_parsed_data(update, context, data)

# --- Fully Interactive Builder Flow ---
async def start_interactive_builder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message or update.callback_query.message
    context.user_data[CONVERSATION_DATA_KEY] = {}
    recent_assets = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "AVAXUSDT"]
    await message.edit_text("🚀 Interactive Builder\n\n1️⃣ Choose an asset:", reply_markup=asset_choice_keyboard(recent_assets))
    return I_ASSET_CHOICE
async def asset_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    choice = query.data.split('_')[1]
    if choice == "new": await query.message.edit_text("✍️ Please type the new asset symbol (e.g., ADAUSDT)."); return I_ASSET_NEW
    context.user_data[CONVERSATION_DATA_KEY]['asset'] = choice; market = context.user_data.get('preferred_market', 'Futures')
    context.user_data[CONVERSATION_DATA_KEY]['market'] = market
    await query.message.edit_text(f"✅ Asset: {choice}\n\n2️⃣ Select direction:", reply_markup=side_market_keyboard(market)); return I_SIDE_MARKET
async def new_asset_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    asset = update.message.text.strip().upper(); context.user_data[CONVERSATION_DATA_KEY]['asset'] = asset
    market = context.user_data.get('preferred_market', 'Futures'); context.user_data[CONVERSATION_DATA_KEY]['market'] = market
    await update.message.reply_text(f"✅ Asset: {asset}\n\n2️⃣ Select direction:", reply_markup=side_market_keyboard(market)); return I_SIDE_MARKET
async def side_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer(); side = query.data.split('_')[1]
    context.user_data[CONVERSATION_DATA_KEY]['side'] = side; asset = context.user_data[CONVERSATION_DATA_KEY]['asset']
    await query.message.edit_text(f"✅ Asset: {asset} ({side})\n\n3️⃣ Send prices:\n`ENTRY STOP TARGETS...`"); return I_PRICES
async def change_market_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await query.message.edit_reply_markup(reply_markup=market_choice_keyboard()); return I_SIDE_MARKET
async def market_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer(); choice = query.data
    market = context.user_data[CONVERSATION_DATA_KEY].get('market', 'Futures')
    if choice != "market_back":
        market = choice.split('_')[1]; context.user_data['preferred_market'] = market
    context.user_data[CONVERSATION_DATA_KEY]['market'] = market
    await query.message.edit_reply_markup(reply_markup=side_market_keyboard(market)); return I_SIDE_MARKET

# ✅ --- NEW HELPER FUNCTION & CORRECTED HANDLER ---
def _parse_price_string(price_str: str) -> float:
    """Helper function to parse a single price string, handling 'k' suffix."""
    s = price_str.strip().lower()
    if 'k' in s:
        return float(s.replace('k', '')) * 1000
    return float(s)

async def prices_received_interactive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        parts = update.message.text.strip().replace(',', ' ').split()
        if len(parts) < 3:
            raise ValueError("At least Entry, Stop, and one Target are required.")
        
        # Use the helper function for all parts
        entry_price = _parse_price_string(parts[0])
        stop_loss = _parse_price_string(parts[1])
        targets = [_parse_price_string(t) for t in parts[2:]]
        
        draft = context.user_data[CONVERSATION_DATA_KEY]
        draft["entry"] = entry_price
        draft["stop_loss"] = stop_loss
        draft["targets"] = targets
        
        return await process_parsed_data(update, context, draft)
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Invalid format. Please try again:\n`ENTRY STOP TARGETS...`")
        return I_PRICES

async def add_notes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    review_key = query.data.split(':')[2]; context.user_data['review_key_for_notes'] = review_key
    await query.message.edit_text(f"{query.message.text}\n\n✍️ Please send your notes now."); return I_NOTES
async def notes_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    notes = update.message.text.strip(); review_key = context.user_data.pop('review_key_for_notes', None)
    if review_key and review_key in context.bot_data:
        draft = context.bot_data[review_key]
        draft['notes'] = notes if notes.lower() not in ['skip', 'none'] else None
        return await process_parsed_data(update, context, draft)
    await update.message.reply_text("Something went wrong. Please start over."); return ConversationHandler.END

# --- Registration Function ---
def register_conversation_handlers(app: Application):
    from .auth import ALLOWED_FILTER
    creation_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("newrec", newrec_entry_point, filters=ALLOWED_FILTER),
            CommandHandler("settings", settings_cmd, filters=ALLOWED_FILTER),
            CallbackQueryHandler(change_method_handler, pattern="^change_method$"),
        ],
        states={
            CHOOSE_METHOD: [CallbackQueryHandler(method_chosen, pattern="^method_")],
            QUICK_COMMAND: [MessageHandler(filters.COMMAND & filters.Regex(r'^\/rec'), quick_command_handler)],
            TEXT_EDITOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, text_editor_handler)],
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
    app.add_handler(CallbackQueryHandler(publish_handler, pattern="^rec:publish:"))
    app.add_handler(CallbackQueryHandler(cancel_publish_handler, pattern="^rec:cancel:"))
    app.add_handler(CallbackQueryHandler(add_notes_handler, pattern="^rec:add_notes:"))
# --- END OF FILE ---