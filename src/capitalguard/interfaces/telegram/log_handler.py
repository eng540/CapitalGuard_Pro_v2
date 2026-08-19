"""R1 direct-input trade logging flow.

The /log command records a trader-owned signal in WATCHLIST status. It reuses
 the existing parser and CreationService forwarding contract, while marking the
 source explicitly as DIRECT_INPUT for later funnel and analytics work.
"""

from __future__ import annotations

import html
import logging
import uuid
from typing import Any, Dict, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from capitalguard.application.services.trade_service import TradeService
from capitalguard.infrastructure.db.uow import uow_transaction
from .auth import require_active_user
from .helpers import get_service
from .keyboards import CallbackBuilder
from .parsers import parse_editor_command, parse_rec_command

log = logging.getLogger(__name__)

LOG_AWAIT_INPUT, LOG_AWAIT_CONFIRM = range(2)
LOG_DRAFT_KEY = "r1_log_draft"
LOG_TOKEN_LENGTH = 12


def _clear_log_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(LOG_DRAFT_KEY, None)


def _parse_log_text(raw_text: str) -> Optional[Dict[str, Any]]:
    """Parse direct input using the same quick/editor contracts as /rec."""
    text = (raw_text or "").strip()
    if not text:
        return None
    parser = parse_editor_command if ":" in text or "\n" in text else parse_rec_command
    parsed = parser(text)
    if not parsed:
        return None
    parsed["source_type"] = "DIRECT_INPUT"
    return parsed


def _review_markup(token: str) -> InlineKeyboardMarkup:
    short_token = token[:LOG_TOKEN_LENGTH]
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ تسجيل في المتابعة",
                    callback_data=CallbackBuilder.create("log", "confirm", short_token),
                ),
                InlineKeyboardButton(
                    "❌ إلغاء",
                    callback_data=CallbackBuilder.create("log", "cancel", short_token),
                ),
            ]
        ]
    )


def _review_text(trade_data: Dict[str, Any]) -> str:
    targets = trade_data.get("targets") or []
    target_text = "، ".join(
        f"{html.escape(str(target.get('price')))} ({target.get('close_percent', 0):g}%)"
        for target in targets
    )
    return (
        "<b>مراجعة الصفقة المباشرة</b>\n\n"
        f"الأصل: <code>{html.escape(str(trade_data.get('asset', '')))}</code>\n"
        f"الاتجاه: <b>{html.escape(str(trade_data.get('side', '')))}</b>\n"
        f"الدخول: <code>{html.escape(str(trade_data.get('entry', '')))}</code>\n"
        f"الوقف: <code>{html.escape(str(trade_data.get('stop_loss', '')))}</code>\n"
        f"الأهداف: <code>{html.escape(target_text)}</code>\n\n"
        "الحالة بعد التأكيد: <b>WATCHLIST</b>\n"
        "المصدر: <b>DIRECT_INPUT</b>"
    )


async def _show_log_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = context.user_data.get(LOG_DRAFT_KEY)
    if not draft:
        return ConversationHandler.END
    message = update.effective_message
    if update.callback_query:
        message = update.callback_query.message
    await message.reply_html(
        _review_text(draft["trade_data"]),
        reply_markup=_review_markup(draft["token"]),
    )
    return LOG_AWAIT_CONFIRM


async def _accept_log_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw_text = (update.message.text or "").strip()
    parsed = _parse_log_text(raw_text)
    if not parsed:
        await update.message.reply_html(
            "❌ لم أستطع تحليل الصفقة. استخدم أحد الشكلين:\n\n"
            "<code>BTCUSDT LONG 90000 89000 91000 92000</code>\n\n"
            "<pre>Asset: BTCUSDT\nSide: LONG\nEntry: 90000\n"
            "SL: 89000\nTPs: 91000 92000</pre>"
        )
        return LOG_AWAIT_INPUT

    context.user_data[LOG_DRAFT_KEY] = {
        "raw_text": raw_text,
        "trade_data": parsed,
        "token": str(uuid.uuid4()),
    }
    try:
        await update.message.delete()
    except Exception:
        pass
    return await _show_log_review(update, context)


@uow_transaction
@require_active_user
async def log_entrypoint(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db_session,
    db_user,
    **kwargs,
) -> int:
    """Start /log, accepting an optional one-line payload after the command."""
    _clear_log_state(context)
    raw_text = " ".join(context.args or []).strip()
    if raw_text:
        parsed = _parse_log_text(raw_text)
        if parsed:
            context.user_data[LOG_DRAFT_KEY] = {
                "raw_text": raw_text,
                "trade_data": parsed,
                "token": str(uuid.uuid4()),
            }
            return await _show_log_review(update, context)

    await update.effective_message.reply_html(
        "📝 <b>تسجيل صفقة مباشرة</b>\n\n"
        "أرسلها كسطر واحد:\n"
        "<code>BTCUSDT LONG 90000 89000 91000 92000</code>\n\n"
        "أو كنموذج متعدد الأسطر:\n"
        "<pre>Asset: BTCUSDT\nSide: LONG\nEntry: 90000\nSL: 89000\nTPs: 91000 92000</pre>"
    )
    return LOG_AWAIT_INPUT


@uow_transaction
@require_active_user
async def log_confirmation_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db_session,
    db_user,
    **kwargs,
) -> int:
    query = update.callback_query
    await query.answer()
    draft = context.user_data.get(LOG_DRAFT_KEY)
    callback = CallbackBuilder.parse(query.data)
    params = callback.get("params", [])
    token = params[0] if params else ""
    if not draft or token != draft["token"][:LOG_TOKEN_LENGTH]:
        await query.edit_message_text("❌ انتهت صلاحية جلسة /log. أعد المحاولة.")
        _clear_log_state(context)
        return ConversationHandler.END

    action = callback.get("action")
    if action == "cancel":
        await query.edit_message_text("❌ تم إلغاء تسجيل الصفقة.")
        _clear_log_state(context)
        return ConversationHandler.END

    if action != "confirm":
        return LOG_AWAIT_CONFIRM

    trade_service = get_service(context, "trade_service", TradeService)
    result = await trade_service.create_trade_from_forwarding_async(
        user_id=str(db_user.telegram_user_id),
        trade_data=draft["trade_data"],
        original_text=draft["raw_text"],
        db_session=db_session,
        status_to_set="WATCHLIST",
        original_published_at=None,
        channel_info=None,
        source_type=draft["trade_data"].get("source_type", "DIRECT_INPUT"),
    )
    if result.get("success"):
        await query.edit_message_text(
            f"✅ تم تسجيل الصفقة في قائمة المتابعة.\nرقم الصفقة: <b>#{result['trade_id']}</b>",
            parse_mode=ParseMode.HTML,
        )
    elif result.get("duplicate"):
        await query.edit_message_text(
            "ℹ️ هذه الصفقة مسجلة مسبقًا خلال نافذة منع التكرار."
        )
    else:
        await query.edit_message_text(
            f"⚠️ تعذر تسجيل الصفقة: {html.escape(str(result.get('error', 'Unknown error')))}",
            parse_mode=ParseMode.HTML,
        )
    _clear_log_state(context)
    return ConversationHandler.END


async def log_cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_log_state(context)
    if update.effective_message:
        await update.effective_message.reply_text("❌ تم إلغاء تسجيل الصفقة.")
    return ConversationHandler.END


def register_log_handler(application: Application) -> None:
    log_conversation = ConversationHandler(
        entry_points=[CommandHandler("log", log_entrypoint)],
        states={
            LOG_AWAIT_INPUT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
                    _accept_log_text,
                )
            ],
            LOG_AWAIT_CONFIRM: [
                CallbackQueryHandler(log_confirmation_handler, pattern=r"^log:(confirm|cancel):")
            ],
        },
        fallbacks=[CommandHandler("cancel", log_cancel_handler)],
        name="direct_trade_log",
        per_user=True,
        per_chat=True,
        conversation_timeout=900,
        persistent=False,
        per_message=False,
    )
    application.add_handler(log_conversation, group=0)
