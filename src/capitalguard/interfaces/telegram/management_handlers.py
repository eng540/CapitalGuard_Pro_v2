# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
import logging
import types
from time import time
from typing import Optional, List, Dict

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
    CommandHandler,
)

from capitalguard.domain.entities import RecommendationStatus, ExitStrategy
from .helpers import get_service
from .keyboards import (
    public_channel_keyboard,
    analyst_control_panel_keyboard,
    analyst_edit_menu_keyboard,
    confirm_close_keyboard,
    build_open_recs_keyboard,
    build_exit_strategy_keyboard,
)
from .ui_texts import build_trade_card_text
from .parsers import parse_number, parse_targets_list
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from .conversation_handlers import show_review_card, CONVERSATION_DATA_KEY

log = logging.getLogger(__name__)

AWAITING_INPUT_KEY = "awaiting_user_input_for"
(PARTIAL_PROFIT_PERCENT, PARTIAL_PROFIT_PRICE) = range(2)

def _parse_tail_int(data: str) -> Optional[int]:
    try:
        return int(data.split(":")[-1])
    except (ValueError, IndexError):
        return None

def _parse_cq_parts(data: str) -> List[str]:
    return data.split(":")

async def _noop_answer(*args, **kwargs):
    return None

def _recently_updated(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int) -> bool:
    key = f"rate_limit_{chat_id}_{message_id}"
    last_update = context.bot_data.get(key, 0)
    now = time()
    if (now - last_update) < 20:
        return True
    context.bot_data[key] = now
    return False

async def navigate_open_recs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = _parse_tail_int(query.data) or 1
    trade_service: TradeService = get_service(context, "trade_service")
    price_service: PriceService = get_service(context, "price_service")
    filters_map = context.user_data.get("last_open_filters", {}) or {}
    items = trade_service.repo.list_open_for_user(user_telegram_id=update.effective_user.id, **filters_map)
    try:
        if not items:
            await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=query.message.message_id, text="✅ لا توجد توصيات مفتوحة تطابق الفلتر الحالي.")
            return
        keyboard = build_open_recs_keyboard(items, current_page=page, price_service=price_service)
        header_text = "<b>📊 لوحة قيادة التوصيات المفتوحة</b>"
        if filters_map:
            filter_text_parts = [f"{k.capitalize()}: {str(v).upper()}" for k, v in filters_map.items()]
            header_text += f"\n<i>فلترة حسب: {', '.join(filter_text_parts)}</i>"
        await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=query.message.message_id, text=f"{header_text}\nاختر توصية لعرض لوحة التحكم الخاصة بها:", reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except BadRequest as e:
        if "Message is not modified" not in str(e): log.warning(f"Error in navigate_open_recs_handler: {e}")
    except Exception as e:
        log.error(f"Unexpected error in navigate_open_recs_handler: {e}", exc_info=True)

async def show_rec_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rec_id = _parse_tail_int(query.data)
    try:
        if rec_id is None:
            await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=query.message.message_id, text="❌ خطأ: لم يتم العثور على رقم التوصية.")
            return
        trade_service: TradeService = get_service(context, "trade_service")
        price_service: PriceService = get_service(context, "price_service")
        rec = trade_service.repo.get_by_id_for_user(rec_id, update.effective_user.id)
        if not rec:
            log.warning("Security: User %s tried to access rec #%s", update.effective_user.id, rec_id)
            await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=query.message.message_id, text=f"❌ لا يمكنك الوصول إلى هذه التوصية.")
            return
        live_price = price_service.get_cached_price(rec.asset.value, rec.market)
        if live_price: setattr(rec, "live_price", live_price)
        text = build_trade_card_text(rec)
        keyboard = analyst_control_panel_keyboard(rec.id) if rec.status != RecommendationStatus.CLOSED else None
        await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=query.message.message_id, text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except BadRequest as e:
        if "Message is not modified" not in str(e): log.warning(f"Error in show_rec_panel_handler: {e}")
    except Exception as e:
        log.error(f"Unexpected error in show_rec_panel_handler: {e}", exc_info=True)

async def update_public_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        rec_id = _parse_tail_int(query.data)
        if rec_id is None: await query.answer("Bad request.", show_alert=True); return
        if _recently_updated(context, query.message.chat_id, query.message.message_id): await query.answer("البيانات محدثة للتو.", show_alert=False); return
        trade_service: TradeService = get_service(context, "trade_service")
        price_service: PriceService = get_service(context, "price_service")
        rec = trade_service.repo.get(rec_id)
        if not rec: await query.answer("التوصية غير موجودة.", show_alert=True); return
        if rec.status == RecommendationStatus.CLOSED: await query.answer("الصفقة مغلقة بالفعل.", show_alert=False); return
        live_price = price_service.get_cached_price(rec.asset.value, rec.market)
        if not live_price: await query.answer("تعذر جلب السعر.", show_alert=True); return
        setattr(rec, "live_price", live_price)
        new_text = build_trade_card_text(rec)
        new_keyboard = public_channel_keyboard(rec.id)
        await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=query.message.message_id, text=new_text, reply_markup=new_keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        await query.answer("تم التحديث ✅")
    except BadRequest as e:
        if "Message is not modified" in str(e): await query.answer("البيانات محدثة بالفعل.")
        else: log.warning(f"Error in update_public_card: {e}")
    except Exception as e:
        log.error(f"Unexpected error in update_public_card: {e}", exc_info=True)
        try: await query.answer("حدث خطأ.", show_alert=True)
        except Exception: pass

async def update_private_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("يجري التحديث...")
    await show_rec_panel_handler(update, context)

async def move_sl_to_be_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("جاري النقل...")
    rec_id = _parse_tail_int(query.data)
    if rec_id:
        trade_service: TradeService = get_service(context, "trade_service")
        rec = trade_service.repo.get(rec_id)
        if rec: trade_service.update_sl(rec_id, rec.entry.value)
    await show_rec_panel_handler(update, context)

async def start_close_flow_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rec_id = _parse_tail_int(query.data)
    if rec_id is None: await query.answer("Bad request.", show_alert=True); return
    context.user_data[AWAITING_INPUT_KEY] = {"action": "close", "rec_id": rec_id, "original_message": query.message}
    await query.answer()
    await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=query.message.message_id, text=f"{query.message.text}\n\n<b>🔻 الرجاء <u>الرد على هذه الرسالة ↩️</u> بسعر الخروج للتوصية #{rec_id}.</b>", parse_mode=ParseMode.HTML)

async def confirm_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = _parse_cq_parts(query.data)
    if len(parts) < 4: await query.answer("Bad request.", show_alert=True); return
    try: rec_id, exit_price = int(parts[2]), parse_number(parts[3])
    except (ValueError, IndexError) as e: await query.answer(f"قيمة غير صالحة: {e}", show_alert=True); return
    await query.answer("جاري إغلاق التوصية...")
    trade_service: TradeService = get_service(context, "trade_service")
    try:
        rec = trade_service.close(rec_id, exit_price)
        final_text = "✅ تم إغلاق التوصية بنجاح.\n\n" + build_trade_card_text(rec)
        await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=query.message.message_id, text=final_text, parse_mode=ParseMode.HTML, reply_markup=None)
    except Exception as e:
        await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=query.message.message_id, text=f"❌ فشل إغلاق التوصية: {e}")
    finally:
        context.user_data.pop(AWAITING_INPUT_KEY, None)

async def cancel_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data.pop(AWAITING_INPUT_KEY, None)
    await show_rec_panel_handler(update, context)

async def show_edit_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rec_id = _parse_tail_int(query.data)
    if rec_id is None: return
    keyboard = analyst_edit_menu_keyboard(rec_id)
    await query.answer()
    await context.bot.edit_message_reply_markup(chat_id=query.message.chat_id, message_id=query.message.message_id, reply_markup=keyboard)

async def back_to_main_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await show_rec_panel_handler(update, context)

async def start_edit_sl_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rec_id = _parse_tail_int(query.data)
    if rec_id is None: return
    context.user_data[AWAITING_INPUT_KEY] = {"action": "edit_sl", "rec_id": rec_id, "original_message": query.message}
    await query.answer()
    await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=query.message.message_id, text=f"{query.message.text}\n\n<b>✏️ الرجاء <u>الرد على هذه الرسالة ↩️</u> بقيمة وقف الخسارة الجديدة للتوصية #{rec_id}.</b>", parse_mode=ParseMode.HTML)

async def start_edit_tp_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rec_id = _parse_tail_int(query.data)
    if rec_id is None: return
    context.user_data[AWAITING_INPUT_KEY] = {"action": "edit_tp", "rec_id": rec_id, "original_message": query.message}
    await query.answer()
    await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=query.message.message_id, text=f"{query.message.text}\n\n<b>🎯 الرجاء <u>الرد على هذه الرسالة ↩️</u> بالأهداف الجديدة للتوصية #{rec_id} (افصل بينها بمسافة).</b>", parse_mode=ParseMode.HTML)

async def strategy_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rec_id = _parse_tail_int(query.data)
    if not rec_id: return
    trade_service: TradeService = get_service(context, "trade_service")
    rec = trade_service.repo.get_by_id_for_user(rec_id, update.effective_user.id)
    if not rec: await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=query.message.message_id, text="التوصية غير موجودة."); return
    strategy_text = "الإغلاق الآلي عند الهدف الأخير" if rec.exit_strategy == ExitStrategy.CLOSE_AT_FINAL_TP else "الإغلاق اليدوي فقط"
    profit_stop_text = f"{rec.profit_stop_price:g}" if getattr(rec, "profit_stop_price", None) is not None else "غير محدد"
    text = (f"<b>Signal #{getattr(rec, 'analyst_rec_id', rec.id)} | {rec.asset.value}</b>\n"
            f"------------------------------------\n"
            f"<b>إدارة استراتيجية الخروج</b>\n\n"
            f"<b>- استراتيجية الإغلاق الحالية:</b> {strategy_text}\n"
            f"<b>- وقف الربح الحالي:</b> {profit_stop_text}\n\n"
            f"اختر الإجراء الذي تريد تنفيذه:")
    keyboard = build_exit_strategy_keyboard(rec)
    await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=query.message.message_id, text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

async def set_strategy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("جاري تغيير الاستراتيجية...")
    parts = _parse_cq_parts(query.data)
    try: rec_id, strategy_value = int(parts[2]), parts[3]
    except Exception: await query.answer("طلب غير صالح.", show_alert=True); return
    trade_service: TradeService = get_service(context, "trade_service")
    trade_service.update_exit_strategy(rec_id, ExitStrategy(strategy_value))
    await strategy_menu_handler(update, context)

async def start_profit_stop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = _parse_cq_parts(query.data)
    try: rec_id = int(parts[2])
    except Exception: await query.answer("طلب غير صالح.", show_alert=True); return
    if len(parts) > 3 and parts[3] == "remove":
        await query.answer("جاري إزالة وقف الربح...")
        trade_service: TradeService = get_service(context, "trade_service")
        trade_service.update_profit_stop(rec_id, None)
        await strategy_menu_handler(update, context)
        return
    context.user_data[AWAITING_INPUT_KEY] = {"action": "profit_stop", "rec_id": rec_id, "original_message": query.message}
    await query.answer()
    await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=query.message.message_id, text=f"{query.message.text}\n\n<b>🛡️ الرجاء <u>الرد على هذه الرسالة ↩️</u> بسعر وقف الربح الجديد.</b>", parse_mode=ParseMode.HTML)

async def unified_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.reply_to_message or not context.user_data:
        return

    # --- Case 1: Reply for managing an existing recommendation ---
    if AWAITING_INPUT_KEY in context.user_data:
        state = context.user_data.pop(AWAITING_INPUT_KEY, None)
        if not state: return
        
        original_message = state.get("original_message")
        if not original_message or update.message.reply_to_message.message_id != original_message.message_id:
            context.user_data[AWAITING_INPUT_KEY] = state
            return

        action, rec_id = state["action"], state["rec_id"]
        user_input = update.message.text.strip()
        try: await update.message.delete()
        except Exception: pass

        trade_service: TradeService = get_service(context, "trade_service")
        dummy_query = types.SimpleNamespace(message=original_message, data=f"rec:show_panel:{rec_id}", answer=_noop_answer, from_user=update.effective_user)
        dummy_update = Update(update.update_id, callback_query=dummy_query)
        
        try:
            if action == "profit_stop":
                price = parse_number(user_input)
                trade_service.update_profit_stop(rec_id, price)
                dummy_update.callback_query.data = f"rec:strategy_menu:{rec_id}"
                await strategy_menu_handler(dummy_update, context)
                return
            if action == "close":
                exit_price = parse_number(user_input)
                text = f"هل تؤكد إغلاق <b>#{rec_id}</b> عند <b>{exit_price:g}</b>؟"
                keyboard = confirm_close_keyboard(rec_id, exit_price)
                await context.bot.edit_message_text(chat_id=original_message.chat_id, message_id=original_message.message_id, text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
                return
            elif action == "edit_sl":
                new_sl = parse_number(user_input)
                trade_service.update_sl(rec_id, new_sl)
            elif action == "edit_tp":
                target_dicts = parse_targets_list(user_input.split())
                trade_service.update_targets(rec_id, target_dicts)
            await show_rec_panel_handler(dummy_update, context)
        except Exception as e:
            log.error(f"Error processing input for action {action}, rec_id {rec_id}: {e}", exc_info=True)
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطأ: {e}")
            await show_rec_panel_handler(dummy_update, context)
        return

    # --- Case 2: Reply with prices for a new recommendation ---
    draft = context.user_data.get(CONVERSATION_DATA_KEY)
    if draft and 'order_type' in draft:
        try:
            order_type = draft['order_type']
            parts = (update.message.text or "").strip().replace(',', ' ').split()
            if order_type == 'MARKET':
                if len(parts) < 2: raise ValueError("At least Stop Loss and one Target are required.")
                draft["entry"] = 0
                draft["stop_loss"] = parse_number(parts[0])
                draft["targets"] = parse_targets_list(parts[1:])
            else:
                if len(parts) < 3: raise ValueError("Entry, Stop, and at least one Target are required.")
                draft["entry"] = parse_number(parts[0])
                draft["stop_loss"] = parse_number(parts[1])
                draft["targets"] = parse_targets_list(parts[2:])
            if not draft["targets"]:
                raise ValueError("No valid targets were parsed.")
            await show_review_card(update, context)
        except (ValueError, IndexError) as e:
            await update.message.reply_text(f"❌ تنسيق أسعار غير صالح: {e}. قم بالرد مرة أخرى بالتنسيق الصحيح.")
        return

async def partial_profit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    rec_id = _parse_tail_int(query.data)
    if rec_id is None: return ConversationHandler.END
    context.user_data['partial_profit_rec_id'] = rec_id
    context.user_data['original_message'] = query.message
    await query.answer()
    await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=query.message.message_id, text=f"{query.message.text}\n\n<b>💰 الرجاء الرد بالنسبة المئوية التي تم جنيها (مثال: 50).</b>", parse_mode=ParseMode.HTML)
    return PARTIAL_PROFIT_PERCENT

async def received_partial_percent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        percentage = parse_number(update.message.text)
        if not (0 < percentage <= 100): raise ValueError("النسبة يجب أن تكون بين 1 و 100.")
        context.user_data['partial_profit_percent'] = percentage
        await update.message.reply_text(f"✅ النسبة: {percentage}%. الآن، الرجاء إرسال سعر جني الربح.")
        return PARTIAL_PROFIT_PRICE
    except (ValueError, IndexError) as e:
        await update.message.reply_text(f"❌ قيمة غير صالحة: {e}. الرجاء إرسال رقم فقط.")
        return PARTIAL_PROFIT_PERCENT

async def received_partial_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        price = parse_number(update.message.text)
        rec_id = context.user_data['partial_profit_rec_id']
        percentage = context.user_data['partial_profit_percent']
        original_message = context.user_data['original_message']
        trade_service: TradeService = get_service(context, "trade_service")
        rec = trade_service.take_partial_profit(rec_id, percentage, price)
        await update.message.reply_text("✅ تم تسجيل جني الأرباح الجزئي بنجاح.")
        dummy_query = types.SimpleNamespace(message=original_message, data=f"rec:show_panel:{rec_id}", answer=_noop_answer, from_user=update.effective_user)
        dummy_update = Update(update.update_id, callback_query=dummy_query)
        await show_rec_panel_handler(dummy_update, context)
    except (ValueError, IndexError) as e:
        await update.message.reply_text(f"❌ قيمة غير صالحة: {e}. الرجاء إرسال سعر صحيح.")
        return PARTIAL_PROFIT_PRICE
    except Exception as e:
        log.error(f"Error in partial profit flow: {e}", exc_info=True)
        await update.message.reply_text(f"❌ حدث خطأ: {e}")
    finally:
        for key in ('partial_profit_rec_id', 'partial_profit_percent', 'original_message'):
            context.user_data.pop(key, None)
    return ConversationHandler.END

async def cancel_partial_profit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    original_message = context.user_data.get('original_message')
    rec_id = context.user_data.get('partial_profit_rec_id')
    for key in ('partial_profit_rec_id', 'partial_profit_percent', 'original_message'):
        context.user_data.pop(key, None)
    await update.message.reply_text("تم إلغاء عملية جني الأرباح.")
    if original_message and rec_id:
        dummy_query = types.SimpleNamespace(message=original_message, data=f"rec:show_panel:{rec_id}", answer=_noop_answer, from_user=update.effective_user)
        dummy_update = Update(update.update_id, callback_query=dummy_query)
        await show_rec_panel_handler(dummy_update, context)
    return ConversationHandler.END

def register_management_handlers(application: Application):
    application.add_handler(CallbackQueryHandler(navigate_open_recs_handler, pattern=r"^open_nav:page:", block=False))
    application.add_handler(CallbackQueryHandler(show_rec_panel_handler, pattern=r"^rec:show_panel:", block=False))
    application.add_handler(CallbackQueryHandler(update_public_card, pattern=r"^rec:update_public:", block=False))
    application.add_handler(CallbackQueryHandler(update_private_card, pattern=r"^rec:update_private:", block=False))
    application.add_handler(CallbackQueryHandler(move_sl_to_be_handler, pattern=r"^rec:move_be:", block=False))
    application.add_handler(CallbackQueryHandler(start_close_flow_handler, pattern=r"^rec:close_start:", block=False))
    application.add_handler(CallbackQueryHandler(show_edit_menu_handler, pattern=r"^rec:edit_menu:", block=False))
    application.add_handler(CallbackQueryHandler(back_to_main_panel_handler, pattern=r"^rec:back_to_main:", block=False))
    application.add_handler(CallbackQueryHandler(start_edit_sl_handler, pattern=r"^rec:edit_sl:", block=False))
    application.add_handler(CallbackQueryHandler(start_edit_tp_handler, pattern=r"^rec:edit_tp:", block=False))
    application.add_handler(CallbackQueryHandler(confirm_close_handler, pattern=r"^rec:confirm_close:", block=False))
    application.add_handler(CallbackQueryHandler(cancel_close_handler, pattern=r"^rec:cancel_close:", block=False))
    application.add_handler(CallbackQueryHandler(strategy_menu_handler, pattern=r"^rec:strategy_menu:", block=False))
    application.add_handler(CallbackQueryHandler(set_strategy_handler, pattern=r"^rec:set_strategy:", block=False))
    application.add_handler(CallbackQueryHandler(start_profit_stop_handler, pattern=r"^rec:set_profit_stop:", block=False))
    
    partial_profit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(partial_profit_start, pattern=r"^rec:close_partial:")],
        states={
            PARTIAL_PROFIT_PERCENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_partial_percent)],
            PARTIAL_PROFIT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_partial_price)],
        },
        fallbacks=[CommandHandler("cancel", cancel_partial_profit)],
        name="partial_profit_conversation",
        per_user=True, per_chat=False, per_message=False,
    )
    application.add_handler(partial_profit_conv)
    
    application.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, unified_reply_handler))
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---