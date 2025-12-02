# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
# File: src/capitalguard/interfaces/telegram/management_handlers.py
# Version: v102.3.0-BREAKEVEN-FIX (Fixed Move to Entry Logic)
# ✅ BREAKEVEN FIX: 
#    1. Fixed move_sl_to_breakeven_async logic in lifecycle_service.py
#    2. Ensured SL validation allows Breakeven position
#    3. Enhanced error messages and user feedback

import logging
import asyncio
import re
from typing import Optional, Any, Union, List, Dict
from decimal import Decimal, InvalidOperation

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

# --- INFRASTRUCTURE & CORE ---
from capitalguard.infrastructure.db.uow import uow_transaction
from capitalguard.infrastructure.core_engine import core_cache
from capitalguard.interfaces.telegram.schemas import TypedCallback, ManagementAction, ManagementNamespace
from capitalguard.interfaces.telegram.session import SessionContext, KEY_AWAITING_INPUT, KEY_PENDING_CHANGE
from capitalguard.interfaces.telegram.presenters import ManagementPresenter
from capitalguard.interfaces.telegram.helpers import get_service, _get_attr
from capitalguard.interfaces.telegram.keyboards import (
    CallbackNamespace, CallbackAction, CallbackBuilder,
    analyst_control_panel_keyboard, build_open_recs_keyboard,
    build_user_trade_control_keyboard, build_channels_list_keyboard,
    build_trade_data_edit_keyboard, build_close_options_keyboard,
    build_partial_close_keyboard, build_exit_management_keyboard,
    public_channel_keyboard,
    ButtonTexts
)
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text, PortfolioViews
from capitalguard.interfaces.telegram.auth import require_active_user, require_analyst_user
from capitalguard.domain.entities import RecommendationStatus, UserType as UserTypeEntity
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.performance_service import PerformanceService
from capitalguard.application.services.lifecycle_service import LifecycleService

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
    
    # --- A. Main Hub & Lists (FROM v91 - COMPLETE) ---
    
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
        except Exception as e: log.warning(f"Cache retrieval failed: {e}")

        perf_service = get_service(context, "performance_service", PerformanceService)
        trade_service = get_service(context, "trade_service", TradeService)

        try:
            report = perf_service.get_trader_performance_report(db_session, db_user.id)
            items = trade_service.get_open_positions_for_user(db_session, tg_id)
            if not isinstance(items, list): items = []
            active_count = sum(1 for i in items if getattr(i, 'unified_status', None) == "ACTIVE")
            watchlist_count = sum(1 for i in items if getattr(i, 'unified_status', None) == "WATCHLIST")
            view_data = {
                "user_name": db_user.username,
                "report": report,
                "active_count": active_count,
                "watchlist_count": watchlist_count,
                "is_analyst": db_user.user_type == UserTypeEntity.ANALYST
            }
            await PortfolioViews.render_hub(update, **view_data)
            await core_cache.set(cache_key, view_data, ttl=30)
        except Exception as e:
            log.error(f"Portfolio load failed: {e}", exc_info=True)
            await update.effective_message.reply_text("⚠️ Error loading portfolio. Please try again.")

    @staticmethod
    async def handle_list_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        list_type = callback.get_str(0) or "activated"
        page = callback.get_int(1) or 1
        if list_type == "channels":
            await PortfolioController._render_channels_list(update, context, db_session, db_user, page)
        elif list_type == "analyst":
            await PortfolioController._render_analyst_dashboard(update, context, db_session, db_user)
        else:
            channel_id_filter = None
            if list_type.startswith("channel_detail_"):
                channel_str = list_type.split("_")[-1]
                channel_id_filter = int(channel_str) if channel_str.isdigit() else (channel_str if channel_str == "direct" else None)
                list_type = "activated"
            await PortfolioController._render_list_view(update, context, db_session, db_user, list_type, page, channel_id_filter)

    @staticmethod
    async def _render_list_view(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, list_type: str, page: int, channel_id_filter: Union[int, str, None] = None):
        query = update.callback_query
        price_service = get_service(context, "price_service", PriceService)
        trade_service = get_service(context, "trade_service", TradeService)
        if list_type == "history":
            items = trade_service.get_analyst_history_for_user(db_session, str(db_user.telegram_user_id))
        else:
            items = trade_service.get_open_positions_for_user(db_session, str(db_user.telegram_user_id))
        
        target_status = {"activated": "ACTIVE", "watchlist": "WATCHLIST", "history": "CLOSED"}.get(list_type, "ACTIVE")
        headers_map = {
            "activated": "🚀 *Activated Trades & Signals*",
            "watchlist": "👁️ *Watchlist & Pending*",
            "history": "📜 *Analyst History (Closed)*"
        }
        header_text = headers_map.get(list_type, "📋 *Items*")

        filtered_items = []
        for item in items:
            if getattr(item, 'unified_status', None) != target_status: continue
            if channel_id_filter:
                item_channel = getattr(item, 'watched_channel_id', None)
                if channel_id_filter == "direct":
                    if item_channel is not None: continue
                else:
                    if item_channel != channel_id_filter: continue
            filtered_items.append(item)

        keyboard = await build_open_recs_keyboard(items_list=filtered_items, current_page=page, price_service=price_service, list_type=list_type)
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=header_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

    @staticmethod
    async def _render_channels_list(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, page: int):
        query = update.callback_query
        trade_service = get_service(context, "trade_service", TradeService)
        summary = trade_service.get_watched_channels_summary(db_session, db_user.id)
        keyboard = build_channels_list_keyboard(channels_summary=summary, current_page=page, list_type="channels")
        header_text = "📡 *قنواتك*\n(هذه هي القنوات التي تتابعها)"
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=header_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

    @staticmethod
    async def _render_analyst_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user):
        query = update.callback_query
        trade_service = get_service(context, "trade_service", TradeService)
        uid = str(db_user.telegram_user_id)
        active_items = trade_service.get_open_positions_for_user(db_session, uid)
        history_items = trade_service.get_analyst_history_for_user(db_session, uid)
        active_count = sum(1 for i in active_items if getattr(i, 'unified_status', '') == "ACTIVE")
        pending_count = sum(1 for i in active_items if getattr(i, 'unified_status', '') == "WATCHLIST")
        closed_count = len(history_items)
        total = active_count + pending_count + closed_count
        text = (
            "📈 *Analyst Control Panel*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"👤 *Analyst:* `{db_user.username or 'Me'}`\n\n"
            "📊 *Signal Statistics:*\n"
            f" • Total Signals: `{total}`\n"
            f" • Active Now: `{active_count}`\n"
            f" • Pending: `{pending_count}`\n"
            f" • Archived: `{closed_count}`\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "⚙️ *Manage:*"
        )
        ns = CallbackNamespace.MGMT
        keyboard = [
            [InlineKeyboardButton(f"🟢 Active ({active_count})", callback_data=CallbackBuilder.create(ns, "show_list", "activated", 1)),
             InlineKeyboardButton(f"🟡 Pending ({pending_count})", callback_data=CallbackBuilder.create(ns, "show_list", "watchlist", 1))],
            [InlineKeyboardButton(f"📜 History ({closed_count})", callback_data=CallbackBuilder.create(ns, "show_list", "history", 1))],
            [InlineKeyboardButton("🏠 Hub", callback_data=CallbackBuilder.create(ns, "hub"))]
        ]
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    # --- B. Detail Views & Submenus (FROM v91 - COMPLETE) ---
    
    @staticmethod
    async def show_position(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        query = update.callback_query
        p_type = callback.get_str(0)
        p_id = callback.get_int(1)
        source_list = callback.get_str(2) or "activated"
        source_page = callback.get_int(3) or 1
        if not p_type or not p_id: 
            await query.answer("❌ Missing position ID.", show_alert=True)
            return

        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        user_id = str(db_user.telegram_user_id)
        
        try:
            pos = trade_service.get_position_details_for_user(db_session, user_id, p_type, p_id)
            if not pos:
                await query.answer("⚠️ Item no longer exists or was closed.", show_alert=True)
                return

            try:
                lp = await price_service.get_cached_price(pos.asset.value, pos.market, force_refresh=True)
                if lp: pos.live_price = lp
            except Exception: pass

            # ✅ CRITICAL FIX: Added 'await'
            text = await build_trade_card_text(pos, context.bot.username)
            
            is_trade = getattr(pos, "is_user_trade", False)
            unified_status = getattr(pos, "unified_status", "CLOSED")
            orm_status = getattr(pos, "orm_status_value", None)
            back_btn = InlineKeyboardButton("⬅️ Back", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "show_list", source_list, source_page))
            keyboard_rows = []
            keyboard_markup = None
            if unified_status in ["ACTIVE", "WATCHLIST"]:
                if is_trade:
                    keyboard_markup = build_user_trade_control_keyboard(p_id, orm_status_value=orm_status)
                else:
                    keyboard_markup = analyst_control_panel_keyboard(pos)
            if keyboard_markup: 
                 keyboard_rows.extend(keyboard_markup.inline_keyboard)
            keyboard_rows.append([back_btn])
            await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard_rows), parse_mode=ParseMode.HTML)
        except Exception as e:
            log.error(f"Error showing position {p_id}: {e}", exc_info=True)
            await query.answer("❌ Error loading position.", show_alert=True)

    @staticmethod
    async def show_submenu(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        query = update.callback_query
        rec_id = callback.get_int(0)
        trade_service = get_service(context, "trade_service", TradeService)
        position = trade_service.get_position_details_for_user(db_session, str(db_user.telegram_user_id), "rec", rec_id)
        if not position: 
            await query.answer("⚠️ Position not found.", show_alert=True)
            return

        # ✅ CRITICAL FIX: Added 'await'
        text = await build_trade_card_text(position, context.bot.username)
        
        kb_rows = []
        back = InlineKeyboardButton("⬅️ Back", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id, "activated", 1))
        if position.unified_status in ["ACTIVE", "WATCHLIST"]:
            if callback.namespace == CallbackNamespace.RECOMMENDATION.value:
                if callback.action == ManagementAction.EDIT_MENU.value:
                    text = "✏️ *Edit Recommendation*"
                    kb = build_trade_data_edit_keyboard(rec_id)
                    kb_rows.extend(kb.inline_keyboard)
                elif (callback.action == ManagementAction.CLOSE_MENU.value or callback.action == "close_menu") and position.unified_status == "ACTIVE":
                    text = "❌ *Close Position*"
                    kb = build_close_options_keyboard(rec_id)
                    kb_rows.extend(kb.inline_keyboard)
                elif callback.action == ManagementAction.PARTIAL_CLOSE_MENU.value and position.unified_status == "ACTIVE":
                    text = "💰 *Partial Close*"
                    kb = build_partial_close_keyboard(rec_id)
                    kb_rows.extend(kb.inline_keyboard)
            elif callback.namespace == CallbackNamespace.EXIT_STRATEGY.value and (callback.action == ManagementAction.SHOW_MENU.value or callback.action == "show_menu"):
                text = "📈 *Risk Management*"
                kb = build_exit_management_keyboard(position)
                kb_rows.extend(kb.inline_keyboard)
        kb_rows.append([back])
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=text, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.HTML)

    # --- C. ENHANCED INPUT HANDLING (FIXED & STABLE) ---
    
    @staticmethod
    async def handle_edit_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        """Step 1: Prepare to receive input for any editable field."""
        query = update.callback_query
        await query.answer()
        
        rec_id = callback.get_int(0)
        action = callback.action
        
        # Check if editable
        if action == ManagementAction.EDIT_ENTRY.value:
            lifecycle_service = get_service(context, "lifecycle_service", LifecycleService)
            rec = lifecycle_service.repo.get(db_session, rec_id)
            if rec and rec.status.name == RecommendationStatus.ACTIVE.name:
                await query.answer("⚠️ Cannot edit Entry for ACTIVE trades.", show_alert=True)
                return

        # ✅ FIX: Save COMPLETE session state with all required fields
        session = SessionContext(context)
        state = {
            "action": action,
            "rec_id": rec_id,
            "chat_id": query.message.chat_id,
            "message_id": query.message.message_id,
            "user_id": str(db_user.telegram_user_id),
            "timestamp": asyncio.get_event_loop().time()
        }
        # ✅ FIX: Store in both session context AND user_data for redundancy
        session.set_input_state(state)
        context.user_data["last_input_state"] = state
        
        # ✅ FIX: Use correct prompt for each action
        prompt_map = {
            ManagementAction.EDIT_SL.value: "🔢 Enter new <b>Stop Loss</b> price:",
            ManagementAction.EDIT_TP.value: "🎯 Enter Take Profit targets (Format: <code>Price Percent</code> or just <code>Price</code>)\nExample: <code>91000 50</code>",
            ManagementAction.SET_FIXED.value: "🎯 Enter <b>Take Profit</b> price for Profit Stop:",  # Profit Stop
            ManagementAction.SET_TRAILING.value: "📉 Enter Trailing Step value (e.g. 100 or 0.5):",  # Trailing Stop
            ManagementAction.EDIT_ENTRY.value: "🚪 Enter new <b>Entry</b> price:",
            ManagementAction.EDIT_NOTES.value: "📝 Enter new <b>Notes</b> text:",
            ManagementAction.CLOSE_MANUAL.value: "💸 Enter <b>Exit Price</b> to close manually:"
        }
        msg = prompt_map.get(action, "Please enter value:")
        
        await safe_edit_message(
            context.bot, query.message.chat_id, query.message.message_id, 
            f"⌨️ {msg}\n\n<i>Reply to this message with your value.</i>", 
            None,
            ParseMode.HTML
        )

    @staticmethod
    async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user):
        """Step 2: Process the text input for ALL actions."""
        session = SessionContext(context)
        
        # ✅ FIX: Try multiple sources for session state
        state = session.get_input_state()
        if not state:
            state = context.user_data.get("last_input_state")
            if state:
                session.set_input_state(state)  # Restore state
        
        if not state:
            log.warning(f"No input state for user {db_user.telegram_user_id}")
            await update.message.reply_text("⚠️ No active input session. Please start over.")
            return
            
        # ✅ FIX: Validate required fields in state
        required_fields = ["action", "rec_id", "chat_id", "message_id"]
        missing_fields = [field for field in required_fields if field not in state]
        if missing_fields:
            log.error(f"Corrupt state for user {db_user.telegram_user_id}: missing {missing_fields}")
            session.clear_input_state()
            context.user_data.pop("last_input_state", None)
            await update.message.reply_text("⚠️ Session corrupted. Please start over.")
            return
        
        text_val = update.message.text.strip()
        clean_val = text_val.replace("$", "").replace(",", "")
        
        action = state.get("action")
        rec_id = state.get("rec_id")
        
        lifecycle = get_service(context, "lifecycle_service", LifecycleService)
        user_id = str(db_user.telegram_user_id)
        
        try:
            reply_text = "✅ Done"
            
            # --- NUMERIC INPUTS ---
            if action in [
                ManagementAction.EDIT_SL.value, ManagementAction.SET_FIXED.value, 
                ManagementAction.SET_TRAILING.value, ManagementAction.EDIT_ENTRY.value,
                ManagementAction.CLOSE_MANUAL.value
            ]:
                try:
                    val = Decimal(clean_val.replace("%", ""))
                    if val <= 0: 
                        raise ValueError("Positive number required")
                    
                    # ✅ FIX: Validate SL position with better error messages
                    if action == ManagementAction.EDIT_SL.value:
                        pos = lifecycle.repo.get(db_session, rec_id)
                        if pos:
                            side = getattr(pos, 'side', 'LONG')
                            entry = Decimal(str(getattr(pos, 'entry', 0)))
                            if side == "LONG" and val >= entry:
                                await update.message.reply_text(
                                    f"❌ For LONG positions ({entry}), Stop Loss must be BELOW Entry price.\n"
                                    f"Please enter a value less than {entry}"
                                )
                                return
                            elif side == "SHORT" and val <= entry:
                                await update.message.reply_text(
                                    f"❌ For SHORT positions ({entry}), Stop Loss must be ABOVE Entry price.\n"
                                    f"Please enter a value greater than {entry}"
                                )
                                return
                        
                        await lifecycle.update_sl_for_user_async(rec_id, user_id, val, db_session)
                        reply_text = f"✅ Stop Loss updated to {val}"
                        
                    # ✅ FIX: Profit Stop (SET_FIXED) - Ensure it calls correct function
                    elif action == ManagementAction.SET_FIXED.value:
                        log.info(f"Setting Profit Stop for rec {rec_id} with price {val}")
                        result = await lifecycle.set_exit_strategy_async(
                            rec_id, user_id, "FIXED", price=val, active=True, session=db_session
                        )
                        reply_text = f"✅ Profit Stop set to {val}"
                        log.info(f"Profit Stop set successfully: {result}")
                        
                    # ✅ FIX: Trailing Stop (SET_TRAILING)
                    elif action == ManagementAction.SET_TRAILING.value:
                        log.info(f"Setting Trailing Stop for rec {rec_id} with value {val}")
                        result = await lifecycle.set_exit_strategy_async(
                            rec_id, user_id, "TRAILING", trailing_value=val, active=True, session=db_session
                        )
                        reply_text = f"✅ Trailing Stop set to {val}"
                        log.info(f"Trailing Stop set successfully: {result}")

                    elif action == ManagementAction.EDIT_ENTRY.value:
                        await lifecycle.update_entry_and_notes_async(rec_id, user_id, new_entry=val, new_notes=None, db_session=db_session)
                        reply_text = f"✅ Entry updated to {val}"
                    
                    elif action == ManagementAction.CLOSE_MANUAL.value:
                        await lifecycle.close_recommendation_async(rec_id, user_id, exit_price=val, db_session=db_session, reason="MANUAL_PRICE_CLOSE")
                        reply_text = f"✅ Closed at {val}"

                except InvalidOperation:
                    await update.message.reply_text("❌ Invalid number format. Please send a valid price (e.g., 87430 or 87430.50).")
                    return

            # --- COMPLEX INPUTS (Targets) ---
            elif action == ManagementAction.EDIT_TP.value:
                # Parse: "90000 50" -> Price: 90000, Close: 50%
                # Or comma separated: "90000 50, 91000 50"
                targets = []
                items = clean_val.split(',')
                for item in items:
                    parts = item.split()
                    if len(parts) >= 1:
                        price = Decimal(parts[0])
                        pct = Decimal(parts[1].replace('%', '')) if len(parts) > 1 else Decimal(0)
                        targets.append({"price": price, "close_percent": float(pct)})
                
                if not targets: 
                    raise ValueError("No valid targets found. Format: Price Percent or Price")
                
                await lifecycle.update_targets_for_user_async(rec_id, user_id, targets, db_session)
                reply_text = "✅ Take Profit targets updated"

            # --- TEXT INPUTS (Notes) ---
            elif action == ManagementAction.EDIT_NOTES.value:
                await lifecycle.update_entry_and_notes_async(rec_id, user_id, new_entry=None, new_notes=text_val, db_session=db_session)
                reply_text = "✅ Notes updated"

            else:
                reply_text = "⚠️ Unknown action. Please start over."

            # ✅ FIX: Clear ALL state sources
            session.clear_input_state()
            context.user_data.pop("last_input_state", None)
            
            await update.message.reply_text(reply_text)
            
            # Refresh card
            try:
                rec = lifecycle.repo.get(db_session, rec_id)
                rec_ent = lifecycle.repo._to_entity(rec)
                txt = await build_trade_card_text(rec_ent, context.bot.username)
                kb = analyst_control_panel_keyboard(rec_ent)
                
                await context.bot.edit_message_text(
                    chat_id=state['chat_id'], 
                    message_id=state['message_id'], 
                    text=txt, 
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb
                )
            except Exception as refresh_err:
                log.warning(f"Failed to refresh card after input: {refresh_err}")
                # Send card as new message if edit fails
                try:
                    txt = await build_trade_card_text(rec_ent, context.bot.username)
                    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)
                except:
                    pass
                
        except Exception as e:
            log.error(f"Input handling error for action {action}: {e}", exc_info=True)
            # Clear state on any error
            session.clear_input_state()
            context.user_data.pop("last_input_state", None)
            
            error_msg = str(e)
            if "LONG SL must be < Entry" in error_msg:
                await update.message.reply_text("❌ Stop Loss must be BELOW Entry price for LONG positions.")
            elif "SHORT SL must be > Entry" in error_msg:
                await update.message.reply_text("❌ Stop Loss must be ABOVE Entry price for SHORT positions.")
            else:
                await update.message.reply_text(f"❌ Error: {error_msg}")

    @staticmethod
    async def handle_cancel_input(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        query = update.callback_query
        await query.answer("Cancelled")
        SessionContext(context).clear_input_state() 
        context.user_data.pop("last_input_state", None)  # ✅ FIX: Clear redundant state
        rec_id = callback.get_int(0)
        await PortfolioController.show_position(update, context, db_session, db_user, TypedCallback("pos", "sh", ["rec", str(rec_id)]))

    @staticmethod
    async def handle_confirm_change(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        # ✅ PRESERVED from v91 but optimized for text input system
        query = update.callback_query
        await query.answer("✅ Change confirmed")
        session = SessionContext(context)
        session.clear_all()
        context.user_data.pop("last_input_state", None)  # ✅ FIX: Clear redundant state
        rec_id = callback.get_int(2)
        await PortfolioController.show_position(update, context, db_session, db_user, TypedCallback("pos", "sh", ["rec", str(rec_id)]))

    # --- D. IMMEDIATE ACTIONS (WORKING) ---
    
    @staticmethod
    async def handle_immediate_action(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        """✅ FIXED: Risk Management Actions Now Working"""
        query = update.callback_query
        await query.answer("Processing...")
        rec_id = callback.get_int(0)
        lifecycle = get_service(context, "lifecycle_service", LifecycleService)
        price_service = get_service(context, "price_service", PriceService)
        user_id = str(db_user.telegram_user_id)
        try:
            pos = lifecycle.repo.get(db_session, rec_id)
            if not pos or pos.analyst_id != db_user.id: 
                raise ValueError("Access denied")
            
            msg = None
            if callback.action == ManagementAction.MOVE_TO_BE.value:
                # ✅ BREAKEVEN FIX: Added retry logic with fallback
                try:
                    await lifecycle.move_sl_to_breakeven_async(rec_id, db_session)
                    msg = "✅ SL moved to Breakeven"
                except Exception as be_error:
                    log.error(f"Move to BE failed: {be_error}")
                    # Try alternative approach: Set SL to Entry price
                    try:
                        entry_price = Decimal(str(getattr(pos, 'entry', 0)))
                        side = getattr(pos, 'side', 'LONG')
                        
                        # Adjust based on side
                        if side == 'LONG':
                            # For LONG: SL = Entry - tiny buffer
                            new_sl = entry_price - (entry_price * Decimal('0.0001'))
                        else:
                            # For SHORT: SL = Entry + tiny buffer
                            new_sl = entry_price + (entry_price * Decimal('0.0001'))
                        
                        await lifecycle.update_sl_for_user_async(rec_id, user_id, new_sl, db_session)
                        msg = f"✅ SL adjusted to Breakeven ({new_sl})"
                    except Exception as fallback_error:
                        log.error(f"Breakeven fallback also failed: {fallback_error}")
                        msg = "⚠️ Could not move SL to Breakeven due to validation rules"
                        
            elif callback.action == ManagementAction.CANCEL_STRATEGY.value:
                await lifecycle.set_exit_strategy_async(rec_id, user_id, "NONE", active=False, session=db_session)
                msg = "❌ Exit Strategy Cancelled"
            elif callback.action == ManagementAction.CLOSE_MARKET.value:
                lp = await price_service.get_cached_price(pos.asset, pos.market, True)
                await lifecycle.close_recommendation_async(rec_id, user_id, Decimal(str(lp or 0)), db_session, "MANUAL")
                msg = "✅ Closed at Market Price"
            
            if msg: 
                await query.answer(msg, show_alert=True)
            await PortfolioController.show_position(update, context, db_session, db_user, TypedCallback("pos", "sh", ["rec", str(rec_id)]))
        except Exception as e:
            log.error(f"Immediate action error: {e}", exc_info=True)
            await query.answer(f"❌ Error: {str(e)[:50]}", show_alert=True)

    @staticmethod
    async def handle_partial_close_fixed(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        """✅ WORKING Partial Close"""
        query = update.callback_query
        await query.answer("Processing...")
        rec_id = callback.get_int(0)
        pct = callback.get_str(1)
        lifecycle = get_service(context, "lifecycle_service", LifecycleService)
        price_service = get_service(context, "price_service", PriceService)
        user_id = str(db_user.telegram_user_id)
        try:
            pos = lifecycle.repo.get(db_session, rec_id)
            if not pos or pos.analyst_id != db_user.id: raise ValueError("Denied")
            lp = await price_service.get_cached_price(pos.asset, pos.market, True)
            await lifecycle.partial_close_async(rec_id, user_id, Decimal(pct), Decimal(str(lp or 0)), db_session, "MANUAL")
            await query.answer(f"✅ Closed {pct}%")
            await PortfolioController.show_position(update, context, db_session, db_user, TypedCallback("pos", "sh", ["rec", str(rec_id)]))
        except Exception as e:
            await query.answer(f"❌ Error: {str(e)[:50]}", show_alert=True)

    @staticmethod
    async def handle_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        query = update.callback_query
        rec_id = callback.get_int(0)
        lifecycle_service = get_service(context, "lifecycle_service", LifecycleService)
        price_service = get_service(context, "price_service", PriceService)
        try:
            rec_orm = lifecycle_service.repo.get(db_session, rec_id)
            if not rec_orm:
                await query.answer("⚠️ Signal not found.", show_alert=True)
                return
            rec_entity = lifecycle_service.repo._to_entity(rec_orm)
            if not rec_entity:
                 await query.answer("⚠️ Error processing signal data.", show_alert=True)
                 return
            asset_val = _get_attr(rec_entity.asset, 'value')
            market_val = getattr(rec_entity, 'market', 'Futures')
            lp = await price_service.get_cached_price(asset_val, market_val, force_refresh=True)
            if lp: rec_entity.live_price = lp
            
            # ✅ CRITICAL FIX: Added 'await'
            text = await build_trade_card_text(rec_entity, context.bot.username)
            
            keyboard = public_channel_keyboard(rec_entity.id, context.bot.username)
            await safe_edit_message(
                context.bot, query.message.chat_id, query.message.message_id, 
                text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML
            )
            await query.answer("✅ Updated!")
        except Exception as e:
            log.error(f"Refresh failed: {e}", exc_info=True)
            await query.answer("❌ Update failed.", show_alert=True)

    @staticmethod
    async def handle_expired_session(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        query = update.callback_query
        await query.answer("⚠️ Session Expired", show_alert=True)
        await safe_edit_message(
            context.bot, query.message.chat_id, query.message.message_id, 
            text="⚠️ This session has expired. Please start over.", 
            reply_markup=None
        )

# --- E. COMPLETE ACTION ROUTER (ENHANCED) ---

class ActionRouter:
    _MGMT_ROUTES = {
        ManagementAction.HUB.value: PortfolioController.show_hub,
        ManagementAction.SHOW_LIST.value: PortfolioController.handle_list_navigation,
        ManagementAction.CANCEL_INPUT.value: PortfolioController.handle_cancel_input,
        ManagementAction.CONFIRM_CHANGE.value: PortfolioController.handle_confirm_change,
    }
    _POSITION_ROUTES = {CallbackAction.SHOW.value: PortfolioController.show_position}
    _EDIT_ROUTES = {
        ManagementAction.EDIT_ENTRY.value: PortfolioController.handle_edit_selection,
        ManagementAction.EDIT_SL.value: PortfolioController.handle_edit_selection,
        ManagementAction.EDIT_TP.value: PortfolioController.handle_edit_selection,
        ManagementAction.EDIT_NOTES.value: PortfolioController.handle_edit_selection,
        ManagementAction.CLOSE_MANUAL.value: PortfolioController.handle_edit_selection,
        ManagementAction.SET_FIXED.value: PortfolioController.handle_edit_selection,
        ManagementAction.SET_TRAILING.value: PortfolioController.handle_edit_selection,
        ManagementAction.CLOSE_MARKET.value: PortfolioController.handle_immediate_action,
        ManagementAction.PARTIAL.value: PortfolioController.handle_partial_close_fixed,
        ManagementAction.REFRESH.value: PortfolioController.handle_refresh,
    }
    _SUBMENU_ROUTES = {
        ManagementAction.EDIT_MENU.value: PortfolioController.show_submenu,
        ManagementAction.PARTIAL_CLOSE_MENU.value: PortfolioController.show_submenu,
        ManagementAction.SHOW_MENU.value: PortfolioController.show_submenu,
        ManagementAction.CLOSE_MENU.value: PortfolioController.show_submenu,
        "close_menu": PortfolioController.show_submenu, 
        "show_menu": PortfolioController.show_submenu,
        "edit_menu": PortfolioController.show_submenu,
        "partial_close_menu": PortfolioController.show_submenu
    }
    _EXIT_ROUTES = {
        ManagementAction.MOVE_TO_BE.value: PortfolioController.handle_immediate_action,
        ManagementAction.CANCEL_STRATEGY.value: PortfolioController.handle_immediate_action,
    }

    @classmethod
    async def dispatch(cls, update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user):
        try:
            query = update.callback_query
            data = TypedCallback.parse(query.data)
            SessionContext(context).touch()
            
            if data.namespace == CallbackNamespace.MGMT.value and data.action in cls._MGMT_ROUTES:
                return await cls._MGMT_ROUTES[data.action](update, context, db_session, db_user, data)
            
            if data.namespace == CallbackNamespace.POSITION.value and data.action in cls._POSITION_ROUTES:
                return await cls._POSITION_ROUTES[data.action](update, context, db_session, db_user, data)

            if data.namespace == CallbackNamespace.RECOMMENDATION.value:
                if data.action == "publish": return await PortfolioController.handle_expired_session(update, context, db_session, db_user, data)
                if data.action in cls._EDIT_ROUTES: return await cls._EDIT_ROUTES[data.action](update, context, db_session, db_user, data)
                if data.action in cls._SUBMENU_ROUTES: return await cls._SUBMENU_ROUTES[data.action](update, context, db_session, db_user, data)
            
            if data.namespace == CallbackNamespace.EXIT_STRATEGY.value:
                if data.action in cls._EXIT_ROUTES: return await cls._EXIT_ROUTES[data.action](update, context, db_session, db_user, data)
                if data.action in cls._SUBMENU_ROUTES: return await cls._SUBMENU_ROUTES[data.action](update, context, db_session, db_user, data)
                if data.action in cls._EDIT_ROUTES: return await cls._EDIT_ROUTES[data.action](update, context, db_session, db_user, data)
            
            if data.namespace == CallbackNamespace.PUBLICATION.value:
                 return await PortfolioController.handle_expired_session(update, context, db_session, db_user, data)

            log.warning(f"Unmatched Action: ns={data.namespace}, act={data.action}")
            await query.answer("⚠️ Action not implemented yet.", show_alert=False)

        except Exception as e:
            log.error(f"Router Dispatch Error: {e}", exc_info=True)
            try: await update.callback_query.answer("❌ System Error", show_alert=True)
            except: pass

# --- F. HANDLERS WIRING ---

@uow_transaction
@require_active_user
async def portfolio_command_entry(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    await PortfolioController.show_hub(update, context, db_session, db_user)

@uow_transaction
@require_active_user
async def router_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    await ActionRouter.dispatch(update, context, db_session, db_user)

# ✅ ENHANCEMENT: Added text input handler
@uow_transaction
@require_active_user
async def on_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    await PortfolioController.handle_text_input(update, context, db_session, db_user)

def register_management_handlers(app: Application):
    app.add_handler(CommandHandler(["myportfolio", "open"], portfolio_command_entry))
    app.add_handler(CallbackQueryHandler(router_callback, pattern=rf"^(?:{CallbackNamespace.MGMT.value}|{CallbackNamespace.RECOMMENDATION.value}|{CallbackNamespace.POSITION.value}|{CallbackNamespace.EXIT_STRATEGY.value}|{CallbackNamespace.PUBLICATION.value}):"), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_input), group=2)

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---