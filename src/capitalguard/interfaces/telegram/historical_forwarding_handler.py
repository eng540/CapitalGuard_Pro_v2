from __future__ import annotations

import logging
from datetime import timezone

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from capitalguard.application.services.historical_forwarding_service import (
    ForwardedMessageInput,
    HistoricalForwardingService,
)
from capitalguard.infrastructure.db.models import ChannelCatalog, UserType
from capitalguard.infrastructure.db.uow import uow_transaction
from capitalguard.interfaces.telegram.admin_commands import _is_admin
from capitalguard.interfaces.telegram.auth import require_active_user

log = logging.getLogger(__name__)

STAGING = 1
BATCH_KEY = "historical_forward_batch_id"


def _allowed(db_user, chat_id: int) -> bool:
    return bool(_is_admin(chat_id) or (db_user and db_user.user_type == UserType.ANALYST))


def _parse_channel_arg(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    args = list(context.args or [])
    return args[0].strip() if args else None


def _resolve_catalog(db_session, value: str):
    catalog = None
    if value.lstrip("-").isdigit():
        catalog = db_session.query(ChannelCatalog).filter(
            ChannelCatalog.telegram_channel_id == int(value)
        ).one_or_none()
    if catalog is None:
        catalog = db_session.query(ChannelCatalog).filter(
            (ChannelCatalog.channel_code == value) | (ChannelCatalog.public_ref == value)
        ).one_or_none()
    return catalog


@uow_transaction
@require_active_user
async def historical_forward_start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    if not update.message or not _allowed(db_user, update.effective_chat.id):
        if update.message:
            await update.message.reply_text("🚫 Historical forwarding is limited to analysts and administrators.")
        return ConversationHandler.END
    channel_arg = _parse_channel_arg(context)
    if not channel_arg:
        await update.message.reply_text("Usage: /historical_forward_start <channel_code|telegram_channel_id>")
        return ConversationHandler.END
    catalog = _resolve_catalog(db_session, channel_arg)
    if not catalog:
        await update.message.reply_text("❌ Channel is not registered in the allow-list.")
        return ConversationHandler.END
    service = HistoricalForwardingService()
    batch = service.start_batch(
        db_session,
        channel_catalog_id=catalog.id,
        requested_by_user_id=db_user.id,
        expected_source_chat_id=catalog.telegram_channel_id,
        mode="BATCH",
    )
    context.user_data[BATCH_KEY] = batch.id
    await update.message.reply_text(
        "✅ Historical batch opened. Forward source messages now.\n"
        "Use /historical_forward_finish to create a dry-run preview, or /historical_forward_cancel to discard staging.\n"
        f"Channel: {catalog.channel_code or catalog.public_ref}"
    )
    return STAGING


@uow_transaction
@require_active_user
async def historical_forward_one_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    if not update.message or not _allowed(db_user, update.effective_chat.id):
        if update.message:
            await update.message.reply_text("🚫 Historical forwarding is limited to analysts and administrators.")
        return ConversationHandler.END
    channel_arg = _parse_channel_arg(context)
    if not channel_arg:
        await update.message.reply_text("Usage: /historical_forward_one <channel_code|telegram_channel_id>")
        return ConversationHandler.END
    catalog = _resolve_catalog(db_session, channel_arg)
    if not catalog:
        await update.message.reply_text("❌ Channel is not registered in the allow-list.")
        return ConversationHandler.END
    batch = HistoricalForwardingService().start_batch(
        db_session,
        channel_catalog_id=catalog.id,
        requested_by_user_id=db_user.id,
        expected_source_chat_id=catalog.telegram_channel_id,
        mode="SINGLE",
        max_records=1,
    )
    context.user_data[BATCH_KEY] = batch.id
    await update.message.reply_text(
        "✅ Single-message historical intake opened. Forward exactly one source message."
    )
    return STAGING


@uow_transaction
@require_active_user
async def historical_forward_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    batch_id = context.user_data.get(BATCH_KEY)
    message = update.message
    if not batch_id or not message or not _allowed(db_user, update.effective_chat.id):
        return ConversationHandler.END
    origin = getattr(message, "forward_origin", None)
    origin_chat = getattr(origin, "chat", None) if origin else None
    origin_type = type(origin).__name__.upper() if origin else "UNKNOWN"
    if "CHANNEL" in origin_type:
        normalized_origin_type = "CHANNEL"
    else:
        normalized_origin_type = origin_type
    source_timestamp = getattr(origin, "date", None) if origin else None
    source_message_id = getattr(origin, "message_id", None) if origin else None
    source_chat_id = getattr(origin_chat, "id", None) if origin_chat else None
    raw_text = message.text or message.caption or ""
    receipt = HistoricalForwardingService().stage_message(
        db_session,
        batch_id=batch_id,
        message=ForwardedMessageInput(
            receiver_chat_id=message.chat_id,
            receiver_message_id=message.message_id,
            forwarding_user_id=db_user.id,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            source_origin_type=normalized_origin_type,
            source_message_timestamp=source_timestamp,
            source_edit_date=getattr(message, "edit_date", None),
            raw_text=raw_text,
            metadata={
                "receiver_date": message.date.astimezone(timezone.utc).isoformat() if message.date else None,
                "receiver_reply_to_message_id": getattr(getattr(message, "reply_to_message", None), "message_id", None),
                "origin_author_signature": getattr(origin, "author_signature", None) if origin else None,
            },
        ),
    )
    await message.reply_text(
        f"📥 {receipt.validation_status}\n"
        f"source_chat={receipt.source_chat_id}\n"
        f"source_message={receipt.source_message_id}\n"
        f"receipt_id={receipt.id}"
    )
    if (receipt.metadata_json or {}).get("mode") == "SINGLE":
        preview = HistoricalForwardingService().preview_batch(db_session, batch_id=batch_id)
        await message.reply_text(
            f"Dry-run ready: accepted={preview.accepted_records}, rejected={preview.rejected_records}. "
            "No live recommendation was created."
        )
        context.user_data.pop(BATCH_KEY, None)
        return ConversationHandler.END
    return STAGING


@uow_transaction
@require_active_user
async def historical_forward_finish_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    batch_id = context.user_data.get(BATCH_KEY)
    if not update.message or not batch_id:
        return ConversationHandler.END
    preview = HistoricalForwardingService().preview_batch(db_session, batch_id=batch_id)
    context.user_data.pop(BATCH_KEY, None)
    await update.message.reply_text(
        "📋 Historical forwarding dry-run ready\n"
        f"total={preview.total_records}\n"
        f"accepted={preview.accepted_records}\n"
        f"rejected={preview.rejected_records}\n"
        f"hidden_origin={preview.hidden_origin_records}\n"
        "The batch is not validated or ingested until owner review."
    )
    return ConversationHandler.END


async def historical_forward_cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop(BATCH_KEY, None)
    if update.message:
        await update.message.reply_text("🛑 Historical forwarding staging cancelled. No evidence was ingested.")
    return ConversationHandler.END


def register_historical_forwarding_handlers(application: Application):
    conversation = ConversationHandler(
        entry_points=[
            CommandHandler("historical_forward_start", historical_forward_start_cmd),
            CommandHandler("historical_forward_one", historical_forward_one_cmd),
        ],
        states={
            STAGING: [
                MessageHandler(filters.FORWARDED & ~filters.COMMAND & filters.ChatType.PRIVATE, historical_forward_message_handler),
                CommandHandler("historical_forward_finish", historical_forward_finish_cmd),
                CommandHandler("historical_forward_cancel", historical_forward_cancel_cmd),
            ]
        },
        fallbacks=[CommandHandler("historical_forward_cancel", historical_forward_cancel_cmd)],
        name="historical_forwarding_intake",
        per_user=True,
        per_chat=True,
        persistent=False,
    )
    application.add_handler(conversation, group=0)
