# src/capitalguard/interfaces/telegram/conversation_handlers.py
# (v29.8 - Final Production Release with UX Hardening)
"""
Final and production-ready version with User Experience hardening.

Changelog:
- [CRITICAL UX FIX] Implemented logic to disable old inline keyboards to prevent "Stale action" errors.
  - When a new conversation starts (`/newrec`), the bot now finds the last conversation message and removes its keyboard.
  - When a conversation ends (publish or cancel), the final message's keyboard is removed.
- [IMPROVEMENT] Added safe handling for `telegram.error.BadRequest` when a message is not modified.
- [CONFIG] Added `per_message=False` to suppress PTBUserWarning.
"""

import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Set

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
from telegram.error import BadRequest

from capitalguard.infrastructure.db.uow import uow_transaction
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
from capitalguard.infrastructure.db.models import UserType
from .parsers import parse_number, parse_targets_list
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.infrastructure.db.repository import ChannelRepository, UserRepository
from .commands import start_cmd, myportfolio_cmd, help_cmd

log = logging.getLogger(__name__)
loge = logging.getLogger("capitalguard.errors")

(SELECT_METHOD, I_ASSET, I_SIDE_MARKET, I_ORDER_TYPE, I_PRICES, I_REVIEW, I_NOTES, I_CHANNEL_PICKER) = range(8)


def get_user_draft(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
    return context.user_data.setdefault("new_rec_draft", {})


def clean_user_state(context: ContextTypes.DEFAULT_TYPE):
    for key in ["new_rec_draft", "last_conv_message", "review_token", "channel_picker_selection"]:
        context.user_data.pop(key, None)


async def _disable_previous_keyboard(context: ContextTypes.DEFAULT_TYPE):
    """Finds the last known conversation message and removes its keyboard to prevent stale interactions."""
    if last_msg_info := context.user_data.get("last_conv_message"):
        chat_id, message_id = last_msg_info
        try:
            await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
        except BadRequest:
            pass # Message might have been deleted or has no keyboard, ignore safely.
        except Exception as e:
            log.warning(f"Could not disable previous keyboard: {e}")


@uow_transaction
@require_active_user
@require_analyst_user
async def newrec_menu_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs) -> int:
    # UX FIX: Disable any lingering keyboards from a previous, unfinished conversation
    await _disable_previous_keyboard(context)
    
    clean_user_state(context)
    sent_message = await update.message.reply_html(
        "🚀 <b>New Recommendation</b>\nChoose an input method:", reply_markup=main_creation_keyboard()
    )
    context.user_data["last_conv_message"] = (sent_message.chat_id, sent_message.message_id)
    return SELECT_METHOD


@uow_transaction
@require_active_user
@require_analyst_user
async def start_interactive_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        recent_assets = trade_service.get_recent_assets_for_user(db_session, str(update.effective_user.id))
        message_obj = update.callback_query.message
        await update.callback_query.answer()
        sent_message = await message_obj.edit_text(
            "<b>Step 1/4: Asset</b>\nSelect or type the asset symbol (e.g., BTCUSDT).",
            reply_markup=asset_choice_keyboard(recent_assets),
            parse_mode="HTML",
        )
        context.user_data["last_conv_message"] = (sent_message.chat_id, sent_message.message_id)
        return I_ASSET
    except Exception as e:
        loge.exception(f"[start_interactive_entrypoint] Error: {e}")
        await update.callback_query.message.reply_text("❌ An unexpected error occurred. Please try again.")
        return ConversationHandler.END


# ... (asset_chosen, side_chosen, order_type_chosen, prices_received are unchanged)
async def asset_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft, message_obj = get_user_draft(context), update.callback_query.message if update.callback_query else update.message
    asset = ""
    try:
        if update.callback_query:
            await update.callback_query.answer()
            asset = update.callback_query.data.split("_", 1)[1]
            if asset.lower() == "new":
                await message_obj.edit_text("✍️ Please type the new asset symbol.")
                return I_ASSET
        else:
            asset = (update.message.text or "").strip().upper()
            try:
                await update.message.delete()
            except Exception:
                pass

        market_data_service = get_service(context, "market_data_service", MarketDataService)
        if not market_data_service.is_valid_symbol(asset, draft.get("market", "Futures")):
            await message_obj.edit_text(f"❌ Symbol '<b>{asset}</b>' is not valid. Please try again.", parse_mode="HTML")
            return I_ASSET

        draft["asset"], draft["market"] = asset, draft.get("market", "Futures")
        await message_obj.edit_text(
            f"✅ Asset: <b>{asset}</b>\n\n<b>Step 2/4: Side</b>\nChoose the trade direction.",
            reply_markup=side_market_keyboard(draft["market"]),
            parse_mode="HTML",
        )
        return I_SIDE_MARKET
    except Exception as e:
        loge.exception(f"[asset_chosen] Error while selecting asset: {e}")
        await message_obj.reply_text("❌ Error processing asset. Please try again.")
        return I_ASSET


async def side_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query, draft = update.callback_query, get_user_draft(context)
        await query.answer()
        draft["side"] = query.data.split("_")[1]
        await query.message.edit_text(
            f"✅ Asset: <b>{draft['asset']} ({draft['side']})</b>\n\n<b>Step 3/4: Order Type</b>\nChoose the entry order type.",
            reply_markup=order_type_keyboard(),
            parse_mode="HTML",
        )
        return I_ORDER_TYPE
    except Exception as e:
        loge.exception(f"[side_chosen] Error: {e}")
        await update.callback_query.message.reply_text("❌ Error selecting side. Please try again.")
        return I_SIDE_MARKET


async def order_type_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query, draft = update.callback_query, get_user_draft(context)
        await query.answer()
        draft["order_type"] = query.data.split("_")[1]
        
        price_service = get_service(context, "price_service", PriceService)
        current_price = await price_service.get_cached_price(draft["asset"], draft.get("market", "Futures"))
        
        current_price_info = ""
        if current_price and draft["order_type"] == "MARKET":
            current_price_info = f"\n\n📊 Current {draft['asset']} Price: ~{current_price:g}"
        
        prompt = (
            f"<b>Step 4/4: Prices</b>\nEnter in one line: <code>STOP TARGETS...</code>\nExample: <code>58k 60k@30 62k@50</code>{current_price_info}"
            if draft["order_type"] == "MARKET"
            else f"<b>Step 4/4: Prices</b>\nEnter in one line: <code>ENTRY STOP TARGETS...</code>\nExample: <code>59k 58k 60k@30 62k@50</code>"
        )
        
        await query.message.edit_text(f"✅ Order Type: <b>{draft['order_type']}</b>\n\n{prompt}", parse_mode="HTML")
        return I_PRICES
    except Exception as e:
        loge.exception(f"[order_type_chosen] Error: {e}")
        await update.callback_query.message.reply_text("❌ Error processing order type.")
        return I_ORDER_TYPE


async def prices_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft, tokens = get_user_draft(context), (update.message.text or "").strip().split()
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        
        if draft["order_type"] == "MARKET":
            if len(tokens) < 2:
                raise ValueError("MARKET format: STOP then TARGETS...\nExample: 58k 60k@30 62k@50")

            stop_loss, targets = parse_number(tokens[0]), parse_targets_list(tokens[1:])
            
            price_service = get_service(context, "price_service", PriceService)
            live_price_float = await price_service.get_cached_price(draft["asset"], draft.get("market", "Futures"), True)
            
            if not live_price_float:
                raise ValueError("Could not fetch live market price.")
            
            live_price = Decimal(str(live_price_float))
            
            trade_service._validate_recommendation_data(draft["side"], live_price, stop_loss, targets)
            draft.update({"entry": live_price, "stop_loss": stop_loss, "targets": targets})
            
        else:
            if len(tokens) < 3:
                raise ValueError("LIMIT/STOP format: ENTRY, STOP, then TARGETS...\nExample: 59k 58k 60k@30 62k@50")
            entry, stop_loss = parse_number(tokens[0]), parse_number(tokens[1])
            targets = parse_targets_list(tokens[2:])
            trade_service._validate_recommendation_data(draft["side"], entry, stop_loss, targets)
            draft.update({"entry": entry, "stop_loss": stop_loss, "targets": targets})
            
        if not draft.get("targets"):
            raise ValueError("No valid targets were parsed.")
            
    except (ValueError, InvalidOperation, TypeError) as e:
        loge.warning(f"[prices_received] Invalid user input: {e}")
        await update.message.reply_text(f"⚠️ {str(e)}\n\nPlease try again.")
        return I_PRICES
    except Exception as e:
        loge.exception(f"[prices_received] Unexpected error: {e}")
        await update.message.reply_text("❌ Unexpected error while parsing prices.")
        return I_PRICES
    return await show_review_card(update, context)


async def show_review_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        message = update.callback_query.message if update.callback_query else update.message
        draft = get_user_draft(context)
        review_token = context.user_data.get("review_token") or str(uuid.uuid4())
        context.user_data["review_token"] = review_token

        price_service = get_service(context, "price_service", PriceService)
        preview_price = await price_service.get_cached_price(draft["asset"], draft.get("market", "Futures"))
        review_text = build_review_text_with_price(draft, preview_price)

        target_chat_id, target_message_id = context.user_data.get("last_conv_message", (message.chat_id, message.message_id))

        try:
            sent_message = await context.bot.edit_message_text(
                chat_id=target_chat_id,
                message_id=target_message_id,
                text=review_text,
                reply_markup=review_final_keyboard(review_token),
                parse_mode="HTML",
            )
            if update.message:
                await update.message.delete()
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                if update.callback_query: await update.callback_query.answer()
                sent_message = message
            else:
                sent_message = await context.bot.send_message(
                    chat_id=target_chat_id,
                    text=review_text,
                    reply_markup=review_final_keyboard(review_token),
                    parse_mode="HTML",
                )

        context.user_data["last_conv_message"] = (sent_message.chat_id, sent_message.message_id)
        return I_REVIEW
    except Exception as e:
        loge.exception(f"[show_review_card] Error: {e}")
        await update.effective_chat.send_message("❌ Error displaying review card.")
        return I_PRICES


@uow_transaction
async def add_notes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        f"{query.message.text}\n\n✍️ Please send your notes for this recommendation.", parse_mode="HTML"
    )
    return I_NOTES


async def notes_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        draft = get_user_draft(context)
        draft["notes"] = update.message.text.strip()
        await update.message.delete()
        return await show_review_card(update, context)
    except Exception as e:
        loge.exception(f"[notes_received] Error: {e}")
        await update.message.reply_text("❌ Error adding notes. Please try again.")
        return I_NOTES


@uow_transaction
async def choose_channels_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    try:
        query = update.callback_query
        await query.answer()
        user = UserRepository(db_session).find_by_telegram_id(query.from_user.id)
        all_channels = ChannelRepository(db_session).list_by_analyst(user.id, only_active=False)
        selected_ids: Set[int] = context.user_data.setdefault(
            "channel_picker_selection", {ch.telegram_channel_id for ch in all_channels if ch.is_active}
        )
        keyboard = build_channel_picker_keyboard(context.user_data["review_token"], all_channels, selected_ids)
        
        await query.edit_message_text("📢 Select channels for publication:", reply_markup=keyboard)
        return I_CHANNEL_PICKER
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            await query.answer()
            return I_CHANNEL_PICKER
        loge.warning(f"[choose_channels_handler] Unhandled BadRequest: {e}")
        await query.message.reply_text("📢 Select channels for publication:", reply_markup=keyboard)
        return I_CHANNEL_PICKER
    except Exception as e:
        loge.exception(f"[choose_channels_handler] Error: {e}")
        await update.callback_query.message.reply_text("❌ Error loading channels.")
        return I_REVIEW


@uow_transaction
async def channel_picker_logic_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    query = update.callback_query
    try:
        await query.answer()
        parts = parse_cq_parts(query.data)
        action, token = parts[1], parts[2]
        selected_ids: Set[int] = context.user_data.get("channel_picker_selection", set())
        
        if action == "toggle":
            channel_id, page = int(parts[3]), int(parts[4])
            if channel_id in selected_ids:
                selected_ids.remove(channel_id)
            else:
                selected_ids.add(channel_id)
        
        page = int(parts[-1]) if action in ("toggle", "nav") else 1
        user = UserRepository(db_session).find_by_telegram_id(query.from_user.id)
        all_channels = ChannelRepository(db_session).list_by_analyst(user.id, only_active=False)
        keyboard = build_channel_picker_keyboard(token, all_channels, selected_ids, page=page)
        
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return I_CHANNEL_PICKER
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            await query.answer()
        else:
            loge.exception(f"[channel_picker_logic_handler] Unhandled BadRequest: {e}")
            await query.message.reply_text("❌ Channel picker failed.")
        return I_CHANNEL_PICKER
    except Exception as e:
        loge.exception(f"[channel_picker_logic_handler] Error: {e}")
        await query.message.reply_text("❌ Channel picker failed.")
        return I_CHANNEL_PICKER


@uow_transaction
async def publish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    query = update.callback_query
    try:
        await query.answer("Publishing...")
        parts = parse_cq_parts(query.data)
        token_in_callback = parts[-1]
        
        if context.user_data.get("review_token")[:len(token_in_callback)] != token_in_callback:
            await query.edit_message_text("❌ Stale action. Please start a new recommendation.", reply_markup=None)
            clean_user_state(context)
            return ConversationHandler.END
            
        draft = get_user_draft(context)
        draft["target_channel_ids"] = context.user_data.get("channel_picker_selection")
        trade_service = get_service(context, "trade_service", TradeService)
        rec, report = await trade_service.create_and_publish_recommendation_async(
            user_id=str(query.from_user.id), db_session=db_session, **draft
        )
        if report.get("success"):
            await query.message.edit_text(f"✅ Recommendation #{rec.id} for <b>{rec.asset.value}</b> published.", parse_mode="HTML", reply_markup=None)
        else:
            await query.message.edit_text(
                f"⚠️ Rec #{rec.id} saved, but publishing failed: {report.get('failed', [{}])[0].get('reason')}",
                parse_mode="HTML",
                reply_markup=None
            )
    except Exception as e:
        loge.exception(f"[publish_handler] Critical failure: {e}")
        await query.message.edit_text(f"❌ A critical error occurred: {e}.", reply_markup=None)
    finally:
        clean_user_state(context)
    return ConversationHandler.END


async def cancel_conv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        message = update.callback_query.message if update.callback_query else update.message
        if update.callback_query:
            await update.callback_query.answer()
        
        # UX FIX: Disable the keyboard on the message that was cancelled.
        await _disable_previous_keyboard(context)

        if last_msg_info := context.user_data.get("last_conv_message"):
            try:
                await context.bot.edit_message_text("Operation cancelled.", chat_id=last_msg_info[0], message_id=last_msg_info[1], reply_markup=None)
            except BadRequest:
                await message.reply_text("Operation cancelled.", reply_markup=ReplyKeyboardRemove())
        else:
            await message.reply_text("Operation cancelled.", reply_markup=ReplyKeyboardRemove())
            
    except Exception as e:
        loge.exception(f"[cancel_conv_handler] Error: {e}")
    finally:
        clean_user_state(context)
    return ConversationHandler.END


def register_conversation_handlers(app: Application):
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("newrec", newrec_menu_entrypoint)],
        states={
            SELECT_METHOD: [CallbackQueryHandler(start_interactive_entrypoint, pattern="^method_interactive")],
            I_ASSET: [
                CallbackQueryHandler(asset_chosen, pattern="^asset_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, asset_chosen),
            ],
            I_SIDE_MARKET: [CallbackQueryHandler(side_chosen, pattern="^side_")],
            I_ORDER_TYPE: [CallbackQueryHandler(order_type_chosen, pattern="^type_")],
            I_PRICES: [MessageHandler(filters.TEXT & ~filters.COMMAND, prices_received)],
            I_REVIEW: [
                CallbackQueryHandler(publish_handler, pattern=r"^rec:publish:"),
                CallbackQueryHandler(choose_channels_handler, pattern=r"^rec:choose_channels:"),
                CallbackQueryHandler(add_notes_handler, pattern=r"^rec:add_notes:"),
                CallbackQueryHandler(cancel_conv_handler, pattern=r"^rec:cancel"),
            ],
            I_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, notes_received)],
            I_CHANNEL_PICKER: [
                CallbackQueryHandler(channel_picker_logic_handler, pattern=r"^pub:"),
                CallbackQueryHandler(show_review_card, pattern=r"^pub:back:"),
                CallbackQueryHandler(publish_handler, pattern=r"^pub:confirm:"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conv_handler),
            CommandHandler("start", start_cmd),
            CommandHandler(["myportfolio", "open"], myportfolio_cmd),
            CommandHandler("help", help_cmd),
        ],
        name="recommendation_creation",
        persistent=False,
        per_user=True,
        per_chat=True,
        per_message=False, # Suppress PTBUserWarning
    )
    app.add_handler(conv_handler)