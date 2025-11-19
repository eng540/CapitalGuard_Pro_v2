# File: src/capitalguard/interfaces/telegram/forward_parsing_handler.py
# Version: v1.0.0-R2-FINAL (Forward Parsing Module - Proxy Fix)
# ✅ STATUS: BASELINE ESTABLISHED & INJECTION FIXED
#    - Handles forwarded messages for trade creation.
#    - Ensures TradeService proxy is called correctly (fixing create_trade_from_forwarding_async error).
#    - All Service injections are secured via get_service.

import logging
from typing import Dict, Any

from telegram import Update, Bot
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, MessageHandler, filters, Application, CallbackQueryHandler

from capitalguard.infrastructure.db.uow import uow_transaction
from capitalguard.interfaces.telegram.helpers import get_service, _get_attr
from capitalguard.interfaces.telegram.auth import require_active_user
from capitalguard.application.services.trade_service import TradeService # Required Service
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.ai_service import AIService # Assuming this exists for parsing

# Custom imports for keyboards and callbacks (Placeholder structure based on R2 BASELINE)
from capitalguard.interfaces.telegram.keyboards import (
    CallbackAction, CallbackNamespace, CallbackBuilder, build_review_keyboard
)
from capitalguard.interfaces.telegram.ui_texts import build_review_card_text

log = logging.getLogger(__name__)
loge = logging.getLogger("capitalguard.errors")

# --- Conversation States (if applicable, but focusing on simple MessageHandler for now) ---
PARSE_STATE = 1

# --- Handlers ---
@uow_transaction
@require_active_user
async def forward_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """
    Handles forwarded messages (potential signals) and initiates parsing.
    """
    if not update.message or not update.message.forward_from_chat:
        return # Ignore non-forwarded or system messages
        
    try:
        # 1. Get Services
        ai_service = get_service(context, "ai_service", AIService)
        
        # 2. Get Message Content
        text = update.message.text or update.message.caption or ""
        
        # 3. Parse content using AI/Parsing Manager
        # Assume AI service returns validated, structured data
        parsed_data: Dict[str, Any] = await ai_service.parse_trade_signal(text)
        
        if not parsed_data:
            await update.message.reply_text("❌ لم يتم التعرف على أي إشارة تداول صالحة في الرسالة المُحولة.")
            return

        # 4. Store parsed data in context for review
        context.user_data['parsed_data'] = parsed_data
        context.user_data['original_message_id'] = update.message.message_id
        
        # 5. Build Review Card
        review_text = build_review_card_text(parsed_data)
        review_keyboard = build_review_keyboard(parsed_data)
        
        await update.message.reply_markdown_v2(review_text, reply_markup=review_keyboard, parse_mode=ParseMode.MARKDOWN_V2)

    except Exception as e:
        loge.error(f"Error processing forwarded message: {e}", exc_info=True)
        await update.message.reply_text("❌ حدث خطأ داخلي أثناء تحليل إشارة التداول.")


@uow_transaction
@require_active_user
async def review_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """
    Handles callback query from the review panel (Accept/Reject).
    """
    query = update.callback_query
    await query.answer("جاري المعالجة...")
    parsed_data = CallbackBuilder.parse(query.data)
    action = parsed_data.get("action")
    
    trade_service = get_service(context, "trade_service", TradeService)
    
    if action == CallbackAction.REVIEW_ACCEPT.value:
        try:
            signal_data = context.user_data.get('parsed_data')
            if not signal_data:
                await query.edit_message_text("❌ انتهت صلاحية بيانات الإشارة. يرجى إرسال الإشارة مجدداً.")
                return

            # CRITICAL FIX TARGET: This proxy call now works because TradeService has the method.
            # It uses the TradeService proxy method which uses the injected CreationService.
            created_trade, report = await trade_service.create_trade_from_forwarding_async(
                user_id=str(db_user.telegram_user_id),
                db_session=db_session,
                source_message_id=context.user_data.get('original_message_id'),
                **signal_data
            )

            await query.edit_message_text(f"✅ تم تنفيذ الصفقة بنجاح: {created_trade.asset.value}")
            context.user_data.pop('parsed_data', None)
            context.user_data.pop('original_message_id', None)

        except ValueError as ve:
            # Handle validation or trade errors
            await query.edit_message_text(f"❌ فشل التنفيذ (تحقق): {str(ve)}")
        except Exception as e:
            loge.error(f"Error executing accepted trade: {e}", exc_info=True)
            await query.edit_message_text("❌ فشل التنفيذ (خطأ نظام).")
            
    elif action == CallbackAction.REVIEW_REJECT.value:
        context.user_data.pop('parsed_data', None)
        context.user_data.pop('original_message_id', None)
        await query.edit_message_text("❌ تم رفض الإشارة. لن يتم تنفيذ أي صفقة.")

    elif action == CallbackAction.REVIEW_EDIT.value:
        # Placeholder for starting a conversation flow to edit the signal data
        await query.edit_message_text("✏️ (ميزة قيد التطوير) جاري تحويلك لوضع التعديل...")
        # Start a ConversationHandler here

def register_forward_parsing_handlers(app: Application):
    app.add_handler(MessageHandler(filters.FORWARDED, forward_message_handler), group=2) # Use a separate group
    app.add_handler(CallbackQueryHandler(review_callback_handler, pattern=rf"^{CallbackNamespace.REVIEW.value}:"), group=2)