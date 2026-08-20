from __future__ import annotations

import logging
from collections import Counter
from datetime import timezone

from sqlalchemy import select
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from capitalguard.application.services.frictionless_ingestion_service import (
    FrictionlessIngestionService,
)
from capitalguard.application.services.historical_parser_service import HistoricalParserService
from capitalguard.application.services.historical_forwarding_service import (
    ForwardedMessageInput,
    HistoricalForwardingService,
)
from capitalguard.infrastructure.db.models import (
    ChannelCatalog,
    HistoricalForwardReceipt,
    HistoricalImportBatch,
    HistoricalShadowChannel,
    UserType,
)
from capitalguard.infrastructure.db.uow import session_scope, uow_transaction
from capitalguard.interfaces.telegram.admin_commands import _is_admin
from capitalguard.interfaces.telegram.auth import require_active_user

log = logging.getLogger(__name__)

STAGING = 1
BATCH_KEY = "historical_forward_batch_id"
AUTO_BATCH_KEY = "frictionless_auto_batch_id"
AUTO_JOB_PREFIX = "frictionless-historical"
AUTO_DEBOUNCE_SECONDS = 3


def historical_forwarding_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return whether this private chat is currently staging historical forwards."""
    return bool(context.user_data.get(BATCH_KEY) or context.user_data.get(AUTO_BATCH_KEY))


def _origin_details(message):
    origin = getattr(message, "forward_origin", None)
    origin_chat = getattr(origin, "chat", None) if origin else None
    origin_type = type(origin).__name__.upper() if origin else "UNKNOWN"
    normalized_origin_type = "CHANNEL" if "CHANNEL" in origin_type else origin_type
    return {
        "origin": origin,
        "origin_type": normalized_origin_type,
        "source_timestamp": getattr(origin, "date", None) if origin else None,
        "source_message_id": getattr(origin, "message_id", None) if origin else None,
        "source_chat_id": getattr(origin_chat, "id", None) if origin_chat else None,
        "source_title": getattr(origin_chat, "title", None) if origin_chat else None,
        "source_username": getattr(origin_chat, "username", None) if origin_chat else None,
    }


def _forwarded_input(message, *, user_id: int, details: dict) -> ForwardedMessageInput:
    origin = details["origin"]
    return ForwardedMessageInput(
        receiver_chat_id=message.chat_id,
        receiver_message_id=message.message_id,
        forwarding_user_id=user_id,
        source_chat_id=details["source_chat_id"],
        source_message_id=details["source_message_id"],
        source_origin_type=details["origin_type"],
        source_message_timestamp=details["source_timestamp"],
        source_edit_date=getattr(message, "edit_date", None),
        raw_text=message.text or message.caption or "",
        metadata={
            "receiver_date": message.date.astimezone(timezone.utc).isoformat() if message.date else None,
            "receiver_reply_to_message_id": getattr(
                getattr(message, "reply_to_message", None), "message_id", None
            ),
            "origin_author_signature": getattr(origin, "author_signature", None) if origin else None,
            "source_title": details["source_title"],
            "source_username": details["source_username"],
            "intake_mode": "DIRECT_AUTO",
        },
    )


def _auto_job_name(chat_id: int, batch_id: int) -> str:
    return f"{AUTO_JOB_PREFIX}:{chat_id}:{batch_id}"


async def _finalize_auto_batch_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data or {}
    batch_id = data.get("batch_id")
    chat_id = data.get("chat_id")
    if not batch_id or not chat_id:
        return
    try:
        with session_scope() as session:
            preview = FrictionlessIngestionService().preview(session, batch_id=batch_id)
            parser = HistoricalParserService()
            parsed_count = 0
            partial_count = 0
            parsed_assets = []
            for record in preview.manifest.get("records", []):
                parsed = parser.parse(record.get("raw_text"))
                if parsed.parse_status == "PARSED":
                    parsed_count += 1
                    asset = (parsed.data or {}).get("asset")
                    if asset:
                        parsed_assets.append(str(asset))
                else:
                    partial_count += 1
            batch = session.get(HistoricalImportBatch, batch_id)
            metadata = dict(batch.metadata_json or {}) if batch else {}
            metadata["parser_preview"] = {
                "parsed_count": parsed_count,
                "partial_count": partial_count,
                "assets": parsed_assets,
                "replay_status": "REPLAY_PENDING" if parsed_count else "NOT_PARSED",
            }
            metadata["intake_status"] = "DRY_RUN"
            metadata["auto_batch_finalized"] = True
            if batch:
                batch.metadata_json = metadata
            source_label = metadata.get("source_title") or metadata.get("source_chat_id") or "Unknown source"
            claim_status = metadata.get("claim_status", "UNVERIFIED")
            text = (
                "📜 Historical preview ready\n"
                f"batch_id={batch_id}\n"
                f"source={source_label}\n"
                f"trust={claim_status}\n"
                f"total={preview.total_records}\n"
                f"accepted={preview.accepted_records}\n"
                f"rejected={preview.rejected_records}\n"
                f"hidden_origin={preview.hidden_origin_records}\n"
                f"parsed={parsed_count}\n"
                f"partial_or_unparsed={partial_count}\n"
                f"assets={', '.join(parsed_assets) or 'N/A'}\n"
                f"replay_status={'REPLAY_PENDING' if parsed_count else 'NOT_PARSED'}\n"
                "No live recommendation or trader position was created."
            )
        await context.bot.send_message(chat_id=chat_id, text=text)
    except Exception:
        log.exception("Automatic historical batch finalization failed for batch %s", batch_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ Historical preview is temporarily unavailable. The staged receipt was retained for review.",
        )
    finally:
        if context.user_data.get(AUTO_BATCH_KEY) == batch_id:
            context.user_data.pop(AUTO_BATCH_KEY, None)


@uow_transaction
@require_active_user
async def direct_historical_forward_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db_session,
    db_user,
    **kwargs,
):
    """Frictionless default: any genuine private channel forward becomes historical staging."""
    message = update.message
    if not message or not update.effective_chat or update.effective_chat.type != "private":
        return

    details = _origin_details(message)
    service = FrictionlessIngestionService()
    source = service.discover_source(
        db_session,
        telegram_channel_id=details["source_chat_id"],
        title=details["source_title"],
        username=details["source_username"],
        discovered_by_user_id=db_user.id,
    )
    batch = service.start_or_reuse_auto_batch(
        db_session,
        source=source,
        requested_by_user_id=db_user.id,
        existing_batch_id=context.user_data.get(AUTO_BATCH_KEY),
    )
    receipt = service.stage_direct_message(
        db_session,
        batch_id=batch.id,
        message=_forwarded_input(message, user_id=db_user.id, details=details),
    )
    context.user_data[AUTO_BATCH_KEY] = batch.id

    job_name = _auto_job_name(message.chat_id, batch.id)
    for job in context.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()
    context.job_queue.run_once(
        _finalize_auto_batch_job,
        when=AUTO_DEBOUNCE_SECONDS,
        data={"batch_id": batch.id, "chat_id": message.chat_id},
        name=job_name,
        chat_id=message.chat_id,
        user_id=message.from_user.id if message.from_user else None,
    )

    status = receipt.validation_status
    source_label = source.title or source.telegram_channel_id or "Unknown source"
    claim_label = source.claim_status
    await message.reply_text(
        "📥 Historical candidate received\n"
        f"source={source_label}\n"
        f"claim_status={claim_label}\n"
        f"status={status}\n"
        f"receipt_id={receipt.id}\n"
        "More forwarded messages received within the next 3 seconds will be grouped automatically."
    )


def _allowed(db_user, chat_id: int) -> bool:
    return bool(_is_admin(chat_id) or (db_user and db_user.user_type == UserType.ANALYST))


def _parse_channel_arg(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    args = list(context.args or [])
    return args[0].strip() if args else None


def _resolve_catalog(db_session, value: str):
    value = value.strip()
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


def _catalog_label(catalog: ChannelCatalog) -> str:
    code = catalog.channel_code or catalog.public_ref or "(no code)"
    title = catalog.title or "Untitled channel"
    return f"{code} | {title} | telegram_channel_id={catalog.telegram_channel_id}"


@uow_transaction
@require_active_user
async def historical_channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Show the allow-listed channel codes and exact Telegram IDs used for matching."""
    if not update.message or not db_user:
        if update.message:
            await update.message.reply_text("🚫 An active account is required to inspect historical sources.")
        return

    catalogs = db_session.execute(
        select(ChannelCatalog)
        .where(ChannelCatalog.is_active.is_(True))
        .order_by(ChannelCatalog.channel_code, ChannelCatalog.id)
    ).scalars().all()
    shadows = db_session.execute(
        select(HistoricalShadowChannel)
        .order_by(HistoricalShadowChannel.last_seen_at.desc(), HistoricalShadowChannel.id.desc())
    ).scalars().all()
    if not catalogs and not shadows:
        await update.message.reply_text("📭 No canonical or shadow historical channels have been discovered yet.")
        return

    lines = ["📚 Historical sources:"]
    lines.extend(f"{index}. {_catalog_label(catalog)} | CANONICAL" for index, catalog in enumerate(catalogs, start=1))
    offset = len(catalogs)
    lines.extend(
        f"{offset + index}. {shadow.title or 'Untitled shadow'} | "
        f"telegram_channel_id={shadow.telegram_channel_id} | {shadow.claim_status}"
        for index, shadow in enumerate(shadows, start=1)
    )
    lines.append("\nThe direct path discovers new channels automatically; start/finish remains available for controlled review.")
    await update.message.reply_text("\n".join(lines))


@uow_transaction
@require_active_user
async def historical_forward_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Show the current staging batch without converting it to a dry-run."""
    if not update.message or not db_user:
        if update.message:
            await update.message.reply_text("🚫 An active account is required to inspect historical status.")
        return

    batch_id = context.user_data.get(BATCH_KEY) or context.user_data.get(AUTO_BATCH_KEY)
    if not batch_id:
        await update.message.reply_text(
            "ℹ️ No historical forwarding batch is active.\n"
            "Forward any channel message here to start automatic historical intake."
        )
        return

    batch = db_session.get(HistoricalImportBatch, batch_id)
    if batch is None:
        context.user_data.pop(BATCH_KEY, None)
        await update.message.reply_text("⚠️ The staging batch no longer exists. Start a new batch.")
        return

    receipts = db_session.execute(
        select(HistoricalForwardReceipt).where(HistoricalForwardReceipt.batch_id == batch.id)
    ).scalars().all()
    counts = Counter(receipt.validation_status for receipt in receipts)
    metadata = batch.metadata_json or {}
    catalog = db_session.get(ChannelCatalog, batch.channel_catalog_id)
    channel_label = _catalog_label(catalog) if catalog else (
        f"{metadata.get('source_title') or 'Shadow channel'} | "
        f"telegram_channel_id={metadata.get('source_chat_id')} | "
        f"{metadata.get('claim_status', 'UNVERIFIED')}"
    )
    await update.message.reply_text(
        "📊 Historical forwarding status\n"
        f"batch_id={batch.id}\n"
        f"status={batch.status}\n"
        f"mode={metadata.get('mode', 'UNKNOWN')}\n"
        f"channel={channel_label}\n"
        f"shadow_channel_id={metadata.get('shadow_channel_id')}\n"
        f"expected_source_chat_id={metadata.get('expected_source_chat_id')}\n"
        f"receipts={len(receipts)}\n"
        f"staged={counts.get('STAGED', 0)}\n"
        f"rejected_channel={counts.get('REJECTED_CHANNEL', 0)}\n"
        f"rejected_origin={counts.get('REJECTED_ORIGIN', 0)}\n"
        f"rejected_timestamp={counts.get('REJECTED_TIMESTAMP', 0)}\n"
        "Continue forwarding, then use /historical_forward_finish."
    )


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
        await update.message.reply_text(
            "❌ Channel is not registered in the allow-list.\n"
            "Use /historical_channels to see the exact channel code and Telegram ID."
        )
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
        "Use /historical_forward_status to inspect the expected source ID.\n"
        "Use /historical_forward_finish to create a dry-run preview, or "
        "/historical_forward_cancel to discard staging.\n"
        f"Channel: {catalog.channel_code or catalog.public_ref}\n"
        f"Expected source_chat_id: {catalog.telegram_channel_id}"
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
        await update.message.reply_text(
            "❌ Channel is not registered in the allow-list.\n"
            "Use /historical_channels to see the exact channel code and Telegram ID."
        )
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
        "✅ Single-message historical intake opened. Forward exactly one source message.\n"
        f"Expected source_chat_id: {catalog.telegram_channel_id}"
    )
    return STAGING


@uow_transaction
@require_active_user
async def historical_forward_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    batch_id = context.user_data.get(BATCH_KEY)
    message = update.message
    if not message:
        return STAGING
    if not batch_id or not _allowed(db_user, update.effective_chat.id):
        if _allowed(db_user, update.effective_chat.id):
            await message.reply_text(
                "⚠️ This message was not attached to an active historical batch. "
                "Use /historical_forward_start first."
            )
        return STAGING

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
                "receiver_reply_to_message_id": getattr(
                    getattr(message, "reply_to_message", None), "message_id", None
                ),
                "origin_author_signature": getattr(origin, "author_signature", None) if origin else None,
            },
        ),
    )
    batch = db_session.get(HistoricalImportBatch, batch_id)
    expected_source_chat_id = (batch.metadata_json or {}).get("expected_source_chat_id") if batch else None
    if receipt.validation_status == "REJECTED_CHANNEL":
        reply = (
            "📥 REJECTED_CHANNEL\n"
            f"source_chat={receipt.source_chat_id}\n"
            f"expected_source_chat={expected_source_chat_id}\n"
            f"source_message={receipt.source_message_id}\n"
            f"receipt_id={receipt.id}\n"
            "The forwarded source ID does not match the selected allow-listed channel. "
            "Use /historical_channels and restart with the matching code."
        )
    elif receipt.validation_status == "REJECTED_ORIGIN":
        reply = (
            "📥 REJECTED_ORIGIN\n"
            f"source_chat={receipt.source_chat_id}\n"
            f"source_message={receipt.source_message_id}\n"
            f"receipt_id={receipt.id}\n"
            "Telegram did not expose a verifiable channel origin. Forward the original message, not a copy."
        )
    else:
        reply = (
            f"📥 {receipt.validation_status}\n"
            f"source_chat={receipt.source_chat_id}\n"
            f"source_message={receipt.source_message_id}\n"
            f"receipt_id={receipt.id}"
        )
    await message.reply_text(reply)
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
        if update.message:
            await update.message.reply_text(
                "ℹ️ No active historical batch. Use /historical_forward_start <channel_code> first."
            )
        return ConversationHandler.END
    preview = HistoricalForwardingService().preview_batch(db_session, batch_id=batch_id)
    context.user_data.pop(BATCH_KEY, None)
    await update.message.reply_text(
        "📋 Historical forwarding dry-run ready\n"
        f"batch_id={preview.batch_id}\n"
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
                MessageHandler(
                    filters.FORWARDED & ~filters.COMMAND & filters.ChatType.PRIVATE,
                    historical_forward_message_handler,
                ),
                CommandHandler("historical_forward_status", historical_forward_status_cmd),
                CommandHandler("historical_forward_finish", historical_forward_finish_cmd),
                CommandHandler("historical_forward_cancel", historical_forward_cancel_cmd),
            ]
        },
        fallbacks=[CommandHandler("historical_forward_cancel", historical_forward_cancel_cmd)],
        name="historical_forwarding_intake",
        per_user=True,
        per_chat=True,
        per_message=False,
        persistent=False,
        conversation_timeout=1800,
    )
    application.add_handler(conversation, group=0)
    application.add_handler(
        MessageHandler(
            filters.FORWARDED & ~filters.COMMAND & filters.ChatType.PRIVATE,
            direct_historical_forward_handler,
        ),
        group=0,
    )
    application.add_handler(CommandHandler("historical_channels", historical_channels_cmd), group=0)
    application.add_handler(CommandHandler("historical_forward_status", historical_forward_status_cmd), group=0)
