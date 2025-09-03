# --- START OF FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
import logging
import uuid
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from .helpers import get_service
from .ui_texts import build_review_text_with_price
from .keyboards import confirm_recommendation_keyboard
from .commands import main_creation_keyboard, change_method_keyboard, newrec_entry_point, settings_cmd
from .parsers import parse_quick_command, parse_text_editor

log = logging.getLogger(__name__)

# --- State Definitions & Keys ---
CHOOSE_METHOD, INTERACTIVE_BUILDER, QUICK_COMMAND, TEXT_EDITOR = range(4)
(ASSET, SIDE, MARKET, PRICES, NOTES, REVIEW) = range(4, 10) # States for interactive flow
USER_PREFERENCE_KEY = "preferred_creation_method"
CONVERSATION_DATA_KEY = "new_rec_draft"

# --- Unified Post-Parsing Logic ---
async def process_parsed_data(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict) -> int:
    """
    A unified function to handle data after it has been parsed by any method.
    It validates, shows a review, and prepares for publishing.
    """
    price_service = get_service(context, "price_service")
    preview_price = price_service.get_preview_price(data["asset"], data.get("market", "Futures"))
    
    review_text = build_review_text_with_price(data, preview_price)
    
    review_key = str(uuid.uuid4())
    context.bot_data[review_key] = data.copy()
    
    message = update.message or update.callback_query.message
    await message.reply_html(
        text=review_text,
        reply_markup=confirm_recommendation_keyboard(review_key)
    )
    return ConversationHandler.END

# --- Handler Functions for the new flow ---
async def method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    choice = query.data.split('_')[1]; context.user_data[USER_PREFERENCE_KEY] = choice
    if choice == "interactive":
        await query.message.edit_text(
            "🚀 Starting Interactive Builder...\n\n1️⃣ Please send the asset symbol (e.g., BTCUSDT).",
            reply_markup=change_method_keyboard()
        ); return ASSET
    elif choice == "quick":
        await query.message.edit_text(
            "⚡️ Quick Command mode.\n\nSend your recommendation starting with `/rec`.",
            reply_markup=change_method_keyboard()
        ); return QUICK_COMMAND
    elif choice == "editor":
        await query.message.edit_text(
            "📋 Text Editor mode.\n\nPaste your recommendation text below.",
            reply_markup=change_method_keyboard()
        ); return TEXT_EDITOR
    return ConversationHandler.END

async def change_method_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await query.message.edit_text("⚙️ Choose your preferred method:", reply_markup=main_creation_keyboard())
    return CHOOSE_METHOD

async def quick_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = parse_quick_command(update.message.text)
    if not data:
        await update.message.reply_text("❌ Invalid format. Please check your command and try again.")
        return QUICK_COMMAND
    return await process_parsed_data(update, context, data)

async def text_editor_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = parse_text_editor(update.message.text)
    if not data:
        await update.message.reply_text("❌ Could not parse text. Ensure required fields (Asset, Side, Entry, Stop, Targets) are present.")
        return TEXT_EDITOR
    return await process_parsed_data(update, context, data)

# --- Handlers for the Interactive Flow ---
async def received_asset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data[CONVERSATION_DATA_KEY] = {"asset": update.message.text.strip().upper()}
    await update.message.reply_text("2️⃣ Great. Now send the side: LONG or SHORT.")
    return SIDE
async def received_side(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    side = update.message.text.strip().upper()
    if side not in ["LONG", "SHORT"]:
        await update.message.reply_text("Invalid side. Please send LONG or SHORT."); return SIDE
    context.user_data[CONVERSATION_DATA_KEY]["side"] = side
    await update.message.reply_text("3️⃣ Perfect. Now send all prices in one message:\n`ENTRY STOP TARGETS...`\nExample: `65000 64000 66k 68000`")
    return PRICES
async def received_prices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        parts = update.message.text.strip().replace(',', ' ').split()
        if len(parts) < 3: raise ValueError("At least Entry, Stop, and one Target are required.")
        entry_price = float(parts[0]); stop_loss = float(parts[1]); targets = []
        for t in parts[2:]:
            t = t.strip().lower()
            if 'k' in t: targets.append(float(t.replace('k', '')) * 1000)
            else: targets.append(float(t))
        context.user_data[CONVERSATION_DATA_KEY]["entry"] = entry_price
        context.user_data[CONVERSATION_DATA_KEY]["stop_loss"] = stop_loss
        context.user_data[CONVERSATION_DATA_KEY]["targets"] = targets
        await update.message.reply_text("4️⃣ Almost done! Send any notes, or type 'skip'.")
        return NOTES
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Invalid price format. Please send numbers in the correct order: `ENTRY STOP TARGETS...`"); return PRICES
async def received_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    notes = update.message.text.strip()
    context.user_data[CONVERSATION_DATA_KEY]["notes"] = notes if notes.lower() not in ['skip', 'none'] else None
    context.user_data[CONVERSATION_DATA_KEY]["market"] = "Futures"
    data = context.user_data.pop(CONVERSATION_DATA_KEY)
    return await process_parsed_data(update, context, data)

# --- Final Publishing and Cancel Handlers ---
async def publish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer("Publishing...")
    review_key = query.data.split(":")[2]; draft = context.bot_data.get(review_key)
    if not draft:
        await query.edit_message_text("❌ Error: Review data not found."); return
    trade_service = get_service(context, "trade_service")
    try:
        entry_val = draft["entry"]
        if isinstance(entry_val, list):
            entry_price = entry_val[0]
            if "notes" not in draft or draft["notes"] is None: draft["notes"] = ""
            draft["notes"] += f"\nEntry Zone: {entry_val[0]}-{entry_val[-1]}"
        else:
            entry_price = entry_val
        rec = trade_service.create_and_publish_recommendation(
            asset=draft["asset"], side=draft["side"], market=draft.get("market", "Futures"),
            entry=entry_price, stop_loss=draft["stop_loss"], targets=draft["targets"],
            notes=draft.get("notes"), user_id=str(query.from_user.id)
        ); await query.edit_message_text(f"✅ Recommendation #{rec.id} published successfully!")
    except Exception as e:
        log.exception("Failed to publish recommendation from conversation.")
        await query.edit_message_text(f"❌ Publication failed: {e}")
    finally: context.bot_data.pop(review_key, None)

async def cancel_publish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    review_key = query.data.split(":")[2]; context.bot_data.pop(review_key, None)
    await query.edit_message_text("Publication cancelled.")
async def cancel_conv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Operation cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def register_conversation_handlers(app: Application):
    # ✅ --- FIX: Import the ALLOWED_FILTER here ---
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
            ASSET: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_asset)],
            SIDE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_side)],
            PRICES: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_prices)],
            NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_notes)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv_handler)],
        allow_reentry=True
    )
    app.add_handler(creation_conv_handler)
    app.add_handler(CallbackQueryHandler(publish_handler, pattern=r"^rec:publish:"))
    app.add_handler(CallbackQueryHandler(cancel_publish_handler, pattern=r"^rec:cancel:"))
# --- END OF FILE ---