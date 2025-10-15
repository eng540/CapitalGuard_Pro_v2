# src/capitalguard/interfaces/telegram/conversation_handlers.py
# (v35.0 – Final Stable Hybrid Implementation)

import logging
import uuid
from decimal import Decimal, InvalidOperation
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from telegram.error import BadRequest, TelegramError

from capitalguard.infrastructure.db.uow import uow_transaction
from capitalguard.infrastructure.db.models import UserType
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.infrastructure.db.repository import ChannelRepository, UserRepository

from .helpers import get_service, parse_cq_parts
from .ui_texts import build_review_text_with_price
from .keyboards import (
    main_creation_keyboard,
    asset_choice_keyboard,
    side_market_keyboard,
    order_type_keyboard,
    review_final_keyboard,
    build_channel_picker_keyboard,
)
from .auth import require_active_user, require_analyst_user
from .parsers import parse_number, parse_targets_list
from .commands import start_cmd, myportfolio_cmd, help_cmd

log = logging.getLogger(__name__)
loge = logging.getLogger("capitalguard.errors")

# ───────────────────────────── STATE UTILITIES ──────────────────────────────

def clean_user_state(context: ContextTypes.DEFAULT_TYPE):
    keys = [k for k in context.user_data if k.startswith("rec_creation_")]
    for k in keys:
        context.user_data.pop(k, None)

async def _disable_prev_keyboard(context: ContextTypes.DEFAULT_TYPE):
    info = context.user_data.get("rec_creation_last_message")
    if not info:
        return
    chat_id, msg_id = info
    try:
        await context.bot.edit_message_reply_markup(chat_id, msg_id, reply_markup=None)
    except (BadRequest, TelegramError):
        pass

# ───────────────────────────── ENTRY COMMAND ────────────────────────────────

@uow_transaction
@require_active_user
@require_analyst_user
async def newrec_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, **kw):
    await _disable_prev_keyboard(context)
    clean_user_state(context)
    msg = await update.message.reply_html(
        "🚀 <b>New Recommendation</b>\nSelect input method:",
        reply_markup=main_creation_keyboard(),
    )
    context.user_data["rec_creation_last_message"] = (msg.chat_id, msg.message_id)
    context.user_data["rec_creation_step"] = "awaiting_method"

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _disable_prev_keyboard(context)
    clean_user_state(context)
    await update.message.reply_text("Operation cancelled.", reply_markup=ReplyKeyboardRemove())

# ───────────────────────────── INTERACTIVE FLOW ─────────────────────────────

@uow_transaction
@require_active_user
@require_analyst_user
async def interactive_method_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kw):
    q = update.callback_query
    await q.answer()
    trade_service = get_service(context, "trade_service", TradeService)
    assets = trade_service.get_recent_assets_for_user(db_session, str(q.from_user.id))
    await q.edit_message_text(
        "<b>Step 1/4: Asset</b>\nSelect or type the asset symbol:",
        reply_markup=asset_choice_keyboard(assets),
        parse_mode="HTML",
    )
    context.user_data["rec_creation_step"] = "awaiting_asset"

@uow_transaction
@require_active_user
@require_analyst_user
async def asset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, **kw):
    q = update.callback_query
    data = context.user_data
    if q:
        await q.answer()
        token = q.data.split("_", 1)[1]
        if token.lower() == "new":
            await q.edit_message_text("✍️ Type the new asset symbol.")
            return
        asset = token.upper()
    else:
        asset = (update.message.text or "").strip().upper()
        try: await update.message.delete()
        except Exception: pass

    mds = get_service(context, "market_data_service", MarketDataService)
    market = data.get("rec_creation_market", "Futures")
    if not mds.is_valid_symbol(asset, market):
        chat_id, msg_id = data["rec_creation_last_message"]
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=f"❌ Invalid symbol <b>{asset}</b>. Try again.",
            parse_mode="HTML",
        )
        return

    data["rec_creation_asset"] = asset
    data["rec_creation_market"] = market
    chat_id, msg_id = data["rec_creation_last_message"]
    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=msg_id,
        text=f"✅ Asset: <b>{asset}</b>\n\n<b>Step 2/4: Side</b>\nChoose direction:",
        reply_markup=side_market_keyboard(market),
        parse_mode="HTML",
    )
    data["rec_creation_step"] = "awaiting_side"

@uow_transaction
@require_active_user
@require_analyst_user
async def side_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, **kw):
    q = update.callback_query
    await q.answer()
    data = context.user_data
    side = q.data.split("_")[1]
    data["rec_creation_side"] = side
    await q.edit_message_text(
        f"✅ {data['rec_creation_asset']} ({side})\n\n<b>Step 3/4: Order Type</b>",
        reply_markup=order_type_keyboard(),
        parse_mode="HTML",
    )
    data["rec_creation_step"] = "awaiting_type"

@uow_transaction
@require_active_user
@require_analyst_user
async def type_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, **kw):
    q = update.callback_query
    await q.answer()
    data = context.user_data
    order_type = q.data.split("_")[1]
    data["rec_creation_order_type"] = order_type

    ps = get_service(context, "price_service", PriceService)
    cur_price = await ps.get_cached_price(data["rec_creation_asset"], data["rec_creation_market"])
    if order_type == "MARKET":
        prompt = f"<b>Step 4/4: Prices</b>\nEnter: <code>STOP TARGETS...</code>\nExample: <code>58k 60k@30 62k@50</code>\n\n📊 Current ~{cur_price:g}"
    else:
        prompt = "<b>Step 4/4: Prices</b>\nEnter: <code>ENTRY STOP TARGETS...</code>\nExample: <code>59k 58k 60k@30 62k@50</code>"

    await q.edit_message_text(f"✅ Order Type: <b>{order_type}</b>\n\n{prompt}", parse_mode="HTML")
    data["rec_creation_step"] = "awaiting_prices"

# ───────────────────────────── PRICE INPUT ─────────────────────────────────

async def prices_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    tokens = (update.message.text or "").strip().split()
    ts = get_service(context, "trade_service", TradeService)
    ps = get_service(context, "price_service", PriceService)
    try:
        if data["rec_creation_order_type"] == "MARKET":
            if len(tokens) < 2:
                raise ValueError("MARKET format: STOP then TARGETS.")
            stop = parse_number(tokens[0])
            targets = parse_targets_list(tokens[1:])
            live = await ps.get_cached_price(data["rec_creation_asset"], data["rec_creation_market"], True)
            if not live:
                raise ValueError("Cannot fetch live price.")
            entry = Decimal(str(live))
            ts._validate_recommendation_data(data["rec_creation_side"], entry, stop, targets)
        else:
            if len(tokens) < 3:
                raise ValueError("LIMIT format: ENTRY STOP TARGETS.")
            entry, stop = parse_number(tokens[0]), parse_number(tokens[1])
            targets = parse_targets_list(tokens[2:])
            ts._validate_recommendation_data(data["rec_creation_side"], entry, stop, targets)

        if not targets:
            raise ValueError("No valid targets.")

        data.update(
            {
                "rec_creation_entry": entry,
                "rec_creation_stop_loss": stop,
                "rec_creation_targets": targets,
            }
        )
        await show_review(update, context)
    except (ValueError, InvalidOperation) as e:
        await update.message.reply_text(f"⚠️ {e}\nTry again.")
    except Exception as e:
        loge.exception(e)
        await update.message.reply_text("❌ Unexpected error.")

# ───────────────────────────── REVIEW ──────────────────────────────────────

async def show_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = context.user_data
    if not d.get("rec_creation_token"):
        d["rec_creation_token"] = str(uuid.uuid4())[:12]
    token = d["rec_creation_token"]

    ps = get_service(context, "price_service", PriceService)
    preview = await ps.get_cached_price(d["rec_creation_asset"], d["rec_creation_market"])
    review_data = {
        "asset": d["rec_creation_asset"],
        "side": d["rec_creation_side"],
        "order_type": d["rec_creation_order_type"],
        "entry": d["rec_creation_entry"],
        "stop_loss": d["rec_creation_stop_loss"],
        "targets": d["rec_creation_targets"],
        "notes": d.get("rec_creation_notes"),
        "market": d["rec_creation_market"],
    }
    text = build_review_text_with_price(review_data, preview)
    chat_id, msg_id = d["rec_creation_last_message"]
    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=msg_id,
        text=text,
        reply_markup=review_final_keyboard(token),
        parse_mode="HTML",
    )
    d["rec_creation_step"] = "awaiting_review_action"

# ───────────────────────────── REVIEW ACTIONS ──────────────────────────────

@uow_transaction
@require_active_user
@require_analyst_user
async def review_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kw):
    q = update.callback_query
    await q.answer()
    data = context.user_data
    action, token = parse_cq_parts(q.data)
    if token != data.get("rec_creation_token"):
        await q.edit_message_text("❌ Stale action. Start new recommendation.")
        clean_user_state(context)
        return

    if action == "edit_notes":
        await q.edit_message_text("✍️ Send your notes (optional):")
        data["rec_creation_step"] = "awaiting_notes"
    elif action == "choose_channels":
        cr = ChannelRepository(db_session)
        channels = cr.get_active_channels_for_user(str(q.from_user.id))
        await q.edit_message_text(
            "📢 Select channels to publish:",
            reply_markup=build_channel_picker_keyboard(channels, token),
            parse_mode="HTML",
        )
        data["rec_creation_step"] = "awaiting_channel_picker"
    elif action == "cancel":
        await q.edit_message_text("Operation cancelled.")
        clean_user_state(context)

async def notes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = context.user_data
    d["rec_creation_notes"] = update.message.text
    await update.message.reply_text("✅ Notes saved.")
    await show_review(update, context)

@uow_transaction
@require_active_user
@require_analyst_user
async def channel_picker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kw):
    q = update.callback_query
    await q.answer()
    data = context.user_data
    action, channel_id, token = parse_cq_parts(q.data)
    if token != data.get("rec_creation_token"):
        await q.edit_message_text("❌ Stale action. Start new recommendation.")
        clean_user_state(context)
        return
    if action == "publish":
        ts = get_service(context, "trade_service", TradeService)
        result = ts.publish_recommendation(
            db_session=db_session,
            user_id=str(q.from_user.id),
            asset=data["rec_creation_asset"],
            side=data["rec_creation_side"],
            order_type=data["rec_creation_order_type"],
            entry=data["rec_creation_entry"],
            stop_loss=data["rec_creation_stop_loss"],
            targets=data["rec_creation_targets"],
            notes=data.get("rec_creation_notes"),
            market=data["rec_creation_market"],
            channel_id=channel_id,
        )
        await q.edit_message_text(f"✅ Published to {result.channel_name}")
        clean_user_state(context)

# ───────────────────────────── REGISTRATION ────────────────────────────────

def register_conversation_handlers(app: Application):
    app.add_handler(CommandHandler("newrec", newrec_handler))
    app.add_handler(CommandHandler("cancel", cancel_handler))
    app.add_handler(CallbackQueryHandler(interactive_method_handler, pattern="^method_interactive"))
    app.add_handler(CallbackQueryHandler(asset_handler, pattern="^asset_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, asset_handler))
    app.add_handler(CallbackQueryHandler(side_handler, pattern="^side_"))
    app.add_handler(CallbackQueryHandler(type_handler, pattern="^type_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, prices_handler))
    app.add_handler(CallbackQueryHandler(review_action_handler, pattern="^review_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, notes_handler))
    app.add_handler(CallbackQueryHandler(channel_picker_handler, pattern="^channel_"))