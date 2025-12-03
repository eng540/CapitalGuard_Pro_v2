# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
# File: src/capitalguard/interfaces/telegram/management_handlers.py
# Version: v106.1.0-CHANNEL-FIX
# ✅ CRITICAL FIXES:
#    1. REFRESH LOGIC: 'Refresh' button in Channels now ONLY updates the price/text (No navigation).
#    2. PUBLIC ACCESS: Followers can click 'Refresh' without getting "Not Found" errors.
#    3. SECURITY: Maintains strict input security for private chats.

import logging
import asyncio
from decimal import Decimal, InvalidOperation
from typing import Optional, Any, Union, List, Dict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CommandHandler,
)

# --- INFRASTRUCTURE ---
from capitalguard.infrastructure.db.uow import uow_transaction
from capitalguard.infrastructure.core_engine import core_cache
from capitalguard.interfaces.telegram.schemas import TypedCallback, ManagementAction, ManagementNamespace
from capitalguard.interfaces.telegram.session import SessionContext
from capitalguard.interfaces.telegram.helpers import get_service
from capitalguard.interfaces.telegram.keyboards import (
    CallbackNamespace, CallbackAction, CallbackBuilder,
    analyst_control_panel_keyboard, build_open_recs_keyboard,
    build_user_trade_control_keyboard, build_channels_list_keyboard,
    build_trade_data_edit_keyboard, build_close_options_keyboard,
    build_partial_close_keyboard, build_exit_management_keyboard,
    public_channel_keyboard
)
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text, PortfolioViews
from capitalguard.interfaces.telegram.auth import require_active_user
from capitalguard.domain.entities import UserType as UserTypeEntity
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.performance_service import PerformanceService
from capitalguard.application.services.lifecycle_service import LifecycleService
# ✅ CENTRAL PARSERS INTEGRATION
from capitalguard.interfaces.telegram.parsers import parse_number, parse_targets_list

log = logging.getLogger(__name__)

async def safe_edit_message(
    bot: Bot, chat_id: int, message_id: int, text: str = None, reply_markup=None, parse_mode: str = ParseMode.HTML
) -> bool:
    if not chat_id or not message_id: return False
    try:
        if text is not None:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup,
                parse_mode=parse_mode, disable_web_page_preview=True
            )
        elif reply_markup is not None:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
        return True
    except BadRequest as e:
        if "message is not modified" in str(e).lower() or "not found" in str(e).lower(): return True
        return False
    except Exception as e:
        log.warning(f"Failed to edit message {chat_id}:{message_id}: {e}", exc_info=True)
        return False

class PortfolioController:
    
    # --- HUB & LISTS ---
    @staticmethod
    async def show_hub(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, *args):
        session = SessionContext(context)
        session.touch()
        user_id = str(db_user.id)
        tg_id = str(db_user.telegram_user_id)
        cache_key = f"portfolio_view:{user_id}"
        
        try:
            cached_view = await core_cache.get(cache_key)
            if cached_view:
                await PortfolioViews.render_hub(update, **cached_view)
                return
        except: pass

        perf = get_service(context, "performance_service", PerformanceService)
        trade = get_service(context, "trade_service", TradeService)

        try:
            report = perf.get_trader_performance_report(db_session, db_user.id)
            items = trade.get_open_positions_for_user(db_session, tg_id) or []
            
            active_count = sum(1 for i in items if getattr(i, 'unified_status', None) == "ACTIVE")
            watchlist_count = sum(1 for i in items if getattr(i, 'unified_status', None) == "WATCHLIST")
            
            data = {
                "user_name": db_user.username,
                "report": report,
                "active_count": active_count,
                "watchlist_count": watchlist_count,
                "is_analyst": db_user.user_type == UserTypeEntity.ANALYST
            }
            await PortfolioViews.render_hub(update, **data)
            await core_cache.set(cache_key, data, ttl=30)
        except Exception as e:
            log.error(f"Hub error: {e}", exc_info=True)
            await update.effective_message.reply_text("⚠️ Error loading portfolio.")

    @staticmethod
    async def handle_list_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        list_type = callback.get_str(0) or "activated"
        page = callback.get_int(1) or 1
        
        if list_type == "channels":
            trade = get_service(context, "trade_service", TradeService)
            summary = trade.get_watched_channels_summary(db_session, db_user.id)
            kb = build_channels_list_keyboard(summary, page, "channels")
            await safe_edit_message(context.bot, update.callback_query.message.chat_id, update.callback_query.message.message_id, "📡 *Channels*", kb, ParseMode.MARKDOWN)
            return
        
        if list_type == "analyst":
            trade = get_service(context, "trade_service", TradeService)
            uid = str(db_user.telegram_user_id)
            active = trade.get_open_positions_for_user(db_session, uid)
            hist = trade.get_analyst_history_for_user(db_session, uid)
            ac = sum(1 for i in active if getattr(i, 'unified_status', '') == "ACTIVE")
            pc = sum(1 for i in active if getattr(i, 'unified_status', '') == "WATCHLIST")
            
            txt = f"📈 <b>Analyst Panel</b>\nActive: {ac} | Pending: {pc} | History: {len(hist)}"
            ns = CallbackNamespace.MGMT
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"🚀 Active ({ac})", callback_data=CallbackBuilder.create(ns, "show_list", "activated", 1))],
                [InlineKeyboardButton(f"🟡 Pending ({pc})", callback_data=CallbackBuilder.create(ns, "show_list", "watchlist", 1))],
                [InlineKeyboardButton(f"📜 History ({len(hist)})", callback_data=CallbackBuilder.create(ns, "show_list", "history", 1))],
                [InlineKeyboardButton("🏠 Hub", callback_data=CallbackBuilder.create(ns, "hub"))]
            ])
            await safe_edit_message(context.bot, update.callback_query.message.chat_id, update.callback_query.message.message_id, txt, kb)
            return

        trade = get_service(context, "trade_service", TradeService)
        price_svc = get_service(context, "price_service", PriceService)
        
        if list_type == "history":
            items = trade.get_analyst_history_for_user(db_session, str(db_user.telegram_user_id))
        else:
            items = trade.get_open_positions_for_user(db_session, str(db_user.telegram_user_id))
            
        target = {"activated": "ACTIVE", "watchlist": "WATCHLIST", "history": "CLOSED"}.get(list_type, "ACTIVE")
        filtered = [i for i in items if getattr(i, 'unified_status', None) == target]
        
        kb = await build_open_recs_keyboard(filtered, page, price_svc, list_type)
        header = f"📋 <b>{list_type.title()} Trades</b>"
        await safe_edit_message(context.bot, update.callback_query.message.chat_id, update.callback_query.message.message_id, header, kb)

    # --- POSITION DETAILS ---
    @staticmethod
    async def show_position(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        p_type, p_id = callback.get_str(0), callback.get_int(1)
        source, page = callback.get_str(2) or "activated", callback.get_int(3) or 1
        
        trade = get_service(context, "trade_service", TradeService)
        pos = trade.get_position_details_for_user(db_session, str(db_user.telegram_user_id), p_type, p_id)
        if not pos:
            await update.callback_query.answer("⚠️ Not found.")
            return

        # Attempt price refresh
        try:
            price_svc = get_service(context, "price_service", PriceService)
            lp = await price_svc.get_cached_price(pos.asset.value, pos.market, force_refresh=True)
            if lp: pos.live_price = lp
        except: pass

        text = await build_trade_card_text(pos, context.bot.username)
        status = getattr(pos, "unified_status", "CLOSED")
        is_trade = getattr(pos, "is_user_trade", False)
        kb = None
        
        if status in ["ACTIVE", "WATCHLIST"]:
            kb = build_user_trade_control_keyboard(p_id, getattr(pos, "orm_status_value", None)) if is_trade else analyst_control_panel_keyboard(pos)
            
        back = [InlineKeyboardButton("⬅️ Back", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "show_list", source, page))]
        if kb:
            new_kb = list(kb.inline_keyboard)
            new_kb.append(back)
            kb = InlineKeyboardMarkup(new_kb)
        else:
            kb = InlineKeyboardMarkup([back])
            
        await safe_edit_message(context.bot, update.callback_query.message.chat_id, update.callback_query.message.message_id, text, kb)

    @staticmethod
    async def show_submenu(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        """Unified submenu handler."""
        rec_id = callback.get_int(0)
        trade_service = get_service(context, "trade_service", TradeService)
        pos = trade_service.get_position_details_for_user(db_session, str(db_user.telegram_user_id), "rec", rec_id)
        
        if not pos:
            await update.callback_query.answer("⚠️ Not found.")
            return

        text = await build_trade_card_text(pos, context.bot.username)
        kb_rows = []
        
        act = callback.action
        if act in [ManagementAction.EDIT_MENU.value, "edit_menu"]:
            kb_rows = build_trade_data_edit_keyboard(rec_id).inline_keyboard
        elif act in [ManagementAction.CLOSE_MENU.value, "close_menu"]:
            kb_rows = build_close_options_keyboard(rec_id).inline_keyboard
        elif act in [ManagementAction.PARTIAL_CLOSE_MENU.value, "partial_close_menu"]:
            kb_rows = build_partial_close_keyboard(rec_id).inline_keyboard
        elif act in [ManagementAction.SHOW_MENU.value, "show_menu"]:
            kb_rows = build_exit_management_keyboard(pos).inline_keyboard

        back = [InlineKeyboardButton("⬅️ Back", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))]
        kb_rows.append(back)
        
        await safe_edit_message(context.bot, update.callback_query.message.chat_id, update.callback_query.message.message_id, text, InlineKeyboardMarkup(kb_rows))

    # --- INPUT HANDLING ---
    @staticmethod
    async def handle_edit_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        query = update.callback_query
        await query.answer()
        rec_id, action = callback.get_int(0), callback.action
        
        session = SessionContext(context)
        state = {"action": action, "rec_id": rec_id, "chat_id": query.message.chat_id, "message_id": query.message.message_id}
        session.set_input_state(state)
        context.user_data["last_input_state"] = state
        
        prompt = {
            ManagementAction.EDIT_SL.value: "🔢 Enter new <b>Stop Loss</b>:",
            ManagementAction.SET_FIXED.value: "🎯 Enter <b>Profit Stop</b> price:",
            ManagementAction.SET_TRAILING.value: "📉 Enter <b>Trailing Step</b>:",
            ManagementAction.EDIT_TP.value: "🎯 Enter Targets (e.g. <code>91k 50%</code> or <code>@</code>):",
            ManagementAction.EDIT_NOTES.value: "📝 Enter <b>Notes</b>:",
            ManagementAction.CLOSE_MANUAL.value: "💸 Enter <b>Exit Price</b>:",
            "add_notes": "📝 Enter <b>Notes</b>:"
        }.get(action, "Enter value:")
        
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, f"{prompt}\n\n<i>Reply here.</i>", None)

    @staticmethod
    async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user):
        session = SessionContext(context)
        state = session.get_input_state() or context.user_data.get("last_input_state")
        
        if not state: return

        text_val = update.message.text.strip()
        action, rec_id = state.get("action"), state.get("rec_id")
        
        if action == "add_notes": action = ManagementAction.EDIT_NOTES.value
        
        lifecycle = get_service(context, "lifecycle_service", LifecycleService)
        uid = str(db_user.telegram_user_id)
        
        try:
            reply = "✅ Done"
            
            if action in [ManagementAction.EDIT_SL.value, ManagementAction.SET_FIXED.value, ManagementAction.SET_TRAILING.value, ManagementAction.CLOSE_MANUAL.value, ManagementAction.EDIT_ENTRY.value]:
                val = parse_number(text_val)
                if not val or val <= 0: 
                    await update.message.reply_text("❌ Invalid number.")
                    return
                
                if action == ManagementAction.EDIT_SL.value:
                    await lifecycle.update_sl_for_user_async(rec_id, uid, val, db_session)
                elif action == ManagementAction.SET_FIXED.value:
                    await lifecycle.set_exit_strategy_async(rec_id, uid, "FIXED", price=val, active=True, session=db_session)
                elif action == ManagementAction.SET_TRAILING.value:
                    await lifecycle.set_exit_strategy_async(rec_id, uid, "TRAILING", trailing_value=val, active=True, session=db_session)
                elif action == ManagementAction.CLOSE_MANUAL.value:
                    await lifecycle.close_recommendation_async(rec_id, uid, exit_price=val, db_session=db_session, reason="MANUAL_PRICE")
                elif action == ManagementAction.EDIT_ENTRY.value:
                    await lifecycle.update_entry_and_notes_async(rec_id, uid, new_entry=val, new_notes=None, db_session=db_session)
            
            elif action == ManagementAction.EDIT_TP.value:
                clean_text = text_val.replace(',', ' ').replace('@', ' @ ')
                tokens = clean_text.split()
                targets = parse_targets_list(tokens)
                if not targets: raise ValueError("Invalid targets")
                await lifecycle.update_targets_for_user_async(rec_id, uid, targets, db_session)
            
            elif action == ManagementAction.EDIT_NOTES.value:
                await lifecycle.update_entry_and_notes_async(rec_id, uid, new_entry=None, new_notes=text_val, db_session=db_session)

            await update.message.reply_text(reply)
            session.clear_input_state()
            context.user_data.pop("last_input_state", None)
            
            try:
                rec = lifecycle.repo.get(db_session, rec_id)
                ent = lifecycle.repo._to_entity(rec)
                txt = await build_trade_card_text(ent, context.bot.username)
                kb = analyst_control_panel_keyboard(ent)
                await context.bot.edit_message_text(chat_id=state['chat_id'], message_id=state['message_id'], text=txt, reply_markup=kb, parse_mode=ParseMode.HTML)
            except: pass

        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")

    # --- IMMEDIATE ACTIONS ---
    @staticmethod
    async def handle_immediate_action(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        query = update.callback_query
        await query.answer("Processing...")
        rec_id = callback.get_int(0)
        lifecycle = get_service(context, "lifecycle_service", LifecycleService)
        price_svc = get_service(context, "price_service", PriceService)
        
        try:
            msg = None
            if callback.action == ManagementAction.MOVE_TO_BE.value:
                await lifecycle.move_sl_to_breakeven_async(rec_id, db_session)
                msg = "🛡️ SL at Breakeven"
            elif callback.action == ManagementAction.CANCEL_STRATEGY.value:
                await lifecycle.set_exit_strategy_async(rec_id, str(db_user.telegram_user_id), "NONE", active=False, session=db_session)
                msg = "❌ Strategy Cancelled"
            elif callback.action == ManagementAction.CLOSE_MARKET.value:
                rec = lifecycle.repo.get(db_session, rec_id)
                lp = await price_svc.get_cached_price(rec.asset, rec.market, True)
                await lifecycle.close_recommendation_async(rec_id, str(db_user.telegram_user_id), Decimal(str(lp or 0)), db_session, "MANUAL")
                msg = "💰 Closed Market"

            if msg: await query.answer(msg, show_alert=True)
            await PortfolioController.show_position(update, context, db_session, db_user, TypedCallback("pos", "sh", ["rec", str(rec_id)]))
        except Exception as e:
            await query.answer(f"❌ Error: {e}", show_alert=True)

    @staticmethod
    async def handle_partial_close_fixed(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        query = update.callback_query
        await query.answer("Processing...")
        rec_id, pct = callback.get_int(0), callback.get_str(1)
        lifecycle = get_service(context, "lifecycle_service", LifecycleService)
        price_svc = get_service(context, "price_service", PriceService)
        
        try:
            rec = lifecycle.repo.get(db_session, rec_id)
            lp = await price_svc.get_cached_price(rec.asset, rec.market, True)
            await lifecycle.partial_close_async(rec_id, str(db_user.telegram_user_id), Decimal(pct), Decimal(str(lp or 0)), db_session, "MANUAL")
            await query.answer(f"✅ Closed {pct}%")
            await PortfolioController.show_position(update, context, db_session, db_user, TypedCallback("pos", "sh", ["rec", str(rec_id)]))
        except Exception as e:
            await query.answer(f"❌ Error: {e}", show_alert=True)

    @staticmethod
    async def handle_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        """
        ✅ FIXED: Determines if refresh is from PRIVATE (Analyst Panel) or CHANNEL (Public Card).
        """
        query = update.callback_query
        rec_id = callback.get_int(0)
        
        # 1. If in PRIVATE chat -> Show Analyst Panel (Full Control)
        if update.effective_chat.type == "private":
            await PortfolioController.show_position(
                update, context, db_session, db_user, 
                TypedCallback("pos", "sh", ["rec", str(rec_id)])
            )
            await query.answer("Refreshed (Analyst View)")
            return

        # 2. If in CHANNEL/GROUP -> Just update the message text/price (Public View)
        #    This fixes the "Not Found" error for followers.
        try:
            lifecycle = get_service(context, "lifecycle_service", LifecycleService)
            price_svc = get_service(context, "price_service", PriceService)
            
            # Use 'get' instead of 'get_position_details' to bypass user checks
            rec_orm = lifecycle.repo.get(db_session, rec_id)
            if not rec_orm:
                await query.answer("⚠️ Not Found")
                return
            
            rec_entity = lifecycle.repo._to_entity(rec_orm)
            # Update Price
            lp = await price_svc.get_cached_price(rec_entity.asset.value, rec_entity.market, force_refresh=True)
            if lp: rec_entity.live_price = lp
            
            # Rebuild Public Card Text
            text = await build_trade_card_text(rec_entity, context.bot.username)
            
            # Keep the PUBLIC keyboard (track signal, etc), NOT the analyst keyboard
            kb = public_channel_keyboard(rec_id, context.bot.username)
            
            await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text, kb)
            await query.answer("✅ Prices Updated")
            
        except Exception as e:
            log.error(f"Public Refresh Failed: {e}")
            await query.answer("⚠️ Update Failed")

class ActionRouter:
    @classmethod
    async def dispatch(cls, update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user):
        query = update.callback_query
        data = TypedCallback.parse(query.data)
        
        # ✅ SPECIAL HANDLING FOR REFRESH (To fix Channel bug)
        if data.action == ManagementAction.REFRESH.value:
             return await PortfolioController.handle_refresh(update, context, db_session, db_user, data)
             
        # Normal Private Chat Handling
        if data.namespace == CallbackNamespace.MGMT.value:
            if data.action == ManagementAction.HUB.value: return await PortfolioController.show_hub(update, context, db_session, db_user)
            if data.action == ManagementAction.SHOW_LIST.value: return await PortfolioController.handle_list_navigation(update, context, db_session, db_user, data)
        if data.namespace == CallbackNamespace.POSITION.value:
            return await PortfolioController.show_position(update, context, db_session, db_user, data)
        
        if data.namespace == CallbackNamespace.RECOMMENDATION.value or data.namespace == CallbackNamespace.EXIT_STRATEGY.value:
            INPUT_ACTIONS = [
                ManagementAction.EDIT_SL.value, ManagementAction.EDIT_TP.value, 
                ManagementAction.EDIT_ENTRY.value, ManagementAction.EDIT_NOTES.value,
                ManagementAction.SET_FIXED.value, ManagementAction.SET_TRAILING.value,
                ManagementAction.CLOSE_MANUAL.value, "add_notes"
            ]
            if data.action in INPUT_ACTIONS:
                return await PortfolioController.handle_edit_selection(update, context, db_session, db_user, data)
            if data.action in [ManagementAction.CLOSE_MARKET.value, ManagementAction.MOVE_TO_BE.value, ManagementAction.CANCEL_STRATEGY.value]:
                return await PortfolioController.handle_immediate_action(update, context, db_session, db_user, data)
            if data.action == ManagementAction.PARTIAL.value:
                return await PortfolioController.handle_partial_close_fixed(update, context, db_session, db_user, data)
            if data.action in ["edit_menu", "close_menu", "partial_close_menu", "show_menu", ManagementAction.EDIT_MENU.value, ManagementAction.CLOSE_MENU.value, ManagementAction.PARTIAL_CLOSE_MENU.value, ManagementAction.SHOW_MENU.value]: 
                return await PortfolioController.show_submenu(update, context, db_session, db_user, data)

@uow_transaction
@require_active_user
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    await ActionRouter.dispatch(update, context, db_session, db_user)

@uow_transaction
@require_active_user
async def on_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    await PortfolioController.handle_text_input(update, context, db_session, db_user)

@uow_transaction
@require_active_user
async def portfolio_command_entry(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    await PortfolioController.show_hub(update, context, db_session, db_user)

def register_management_handlers(app: Application):
    app.add_handler(CommandHandler(["myportfolio", "open"], portfolio_command_entry))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=rf"^(?:{CallbackNamespace.MGMT.value}|{CallbackNamespace.RECOMMENDATION.value}|{CallbackNamespace.POSITION.value}|{CallbackNamespace.EXIT_STRATEGY.value}|{CallbackNamespace.PUBLICATION.value}):"), group=1)
    
    # ✅ SECURITY FIX: Only listen to text in PRIVATE chats.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, on_text_input), group=2)

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---