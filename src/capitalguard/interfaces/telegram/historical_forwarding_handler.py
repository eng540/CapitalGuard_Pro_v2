from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from capitalguard.application.services.frictionless_ingestion_service import (
    FrictionlessIngestionService,
)
from capitalguard.application.services.historical_parser_service import HistoricalParserService
from capitalguard.application.services.historical_semantic_materialization_service import HistoricalSemanticMaterializationService
from capitalguard.application.services.image_parsing_service import ImageParsingService
from capitalguard.application.services.historical_replay_gate_service import HistoricalReplayGateService
from capitalguard.application.services.live_review_service import LiveReviewService
from capitalguard.application.services.historical_evidence_ingestion_service import (
    HistoricalEvidenceIngestionError,
    HistoricalEvidenceIngestionService,
)
from capitalguard.application.services.historical_owner_review_service import (
    HistoricalOwnerReviewError,
    HistoricalOwnerReviewService,
)
from capitalguard.application.services.parsing_service import ParsingService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.historical_forwarding_service import (
    ForwardedMessageInput,
    HistoricalForwardingService,
)
from capitalguard.application.services.historical_signal_service import HistoricalSignalValidationError
from capitalguard.infrastructure.db.models import (
    ChannelCatalog,
    HistoricalForwardReceipt,
    HistoricalImportBatch,
    HistoricalShadowChannel,
    UserType,
)
from capitalguard.infrastructure.db.repository import ParsingRepository
from capitalguard.infrastructure.db.uow import session_scope, uow_transaction
from capitalguard.interfaces.telegram.admin_commands import _is_admin
from capitalguard.interfaces.telegram.auth import require_active_user
from capitalguard.interfaces.telegram.presentation_adapter import build_batch_summary, build_single_result_card

log = logging.getLogger(__name__)

STAGING = 1
BATCH_KEY = "historical_forward_batch_id"
AUTO_BATCH_KEY = "frictionless_auto_batch_id"
AUTO_JOB_PREFIX = "frictionless-historical"
AUTO_DEBOUNCE_SECONDS = 3
PREVIEW_ACTION_PREFIX = "historical-preview"
AUTO_PROGRESS_MESSAGE_KEY = "frictionless_auto_progress_message_id"


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
    photos = list(getattr(message, "photo", None) or [])
    largest_photo = photos[-1] if photos else None
    media = None
    if largest_photo is not None:
        media = {
            "media_type": "PHOTO",
            "file_id": getattr(largest_photo, "file_id", None),
            "media_unique_id": getattr(largest_photo, "file_unique_id", None),
            "width": getattr(largest_photo, "width", None),
            "height": getattr(largest_photo, "height", None),
        }
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
            "media": media,
        },
    )


async def _materialize_historical_content(db_session, db_user, receipt: HistoricalForwardReceipt):
    """Materialize staged text and, when present, image through existing contracts."""
    if receipt.validation_status != "STAGED":
        return None
    media = (receipt.metadata_json or {}).get("media") or {}
    file_id = media.get("file_id")
    image_result = None
    try:
        if file_id:
            image_result = await ImageParsingService().parse_image_from_file_id(db_user.id, str(file_id))
        forwarding = HistoricalForwardingService()
        revision = forwarding.message_foundation_service.record_receipt(db_session, receipt=receipt)
        projection = HistoricalSemanticMaterializationService().materialize_revision(
            db_session,
            revision_id=revision.id,
            image_result=image_result,
            image_provenance={
                "media_id": media.get("media_unique_id"),
                "file_id": media.get("file_id"),
                "media_type": media.get("media_type"),
                "source_message_id": receipt.source_message_id,
                "source_chat_id": receipt.source_chat_id,
                "parser_path": (image_result or {}).get("parser_path_used"),
            } if file_id else None,
        )
        receipt_metadata = dict(receipt.metadata_json or {})
        receipt_metadata["semantic_projection"] = projection
        receipt_metadata["historical_preview"] = {
            **projection,
            "source_verification": "VERIFIED_PROVENANCE"
            if receipt.source_chat_id is not None and receipt.source_message_id is not None
            else "UNVERIFIED",
            "extraction_source": "TELEGRAM_IMAGE_OR_TEXT_MATERIALIZATION",
        }
        receipt.metadata_json = receipt_metadata
        db_session.flush()
        return projection
    except Exception:
        log.exception("Historical semantic materialization failed for receipt %s", receipt.id)
        metadata = dict(receipt.metadata_json or {})
        metadata["semantic_materialization"] = {
            "status": "FAILED",
            "modality": "IMAGE" if file_id else "TEXT",
            "error": "HISTORICAL_SEMANTIC_MATERIALIZATION_FAILED",
        }
        receipt.metadata_json = metadata
        return None


def _auto_job_name(chat_id: int, batch_id: int) -> str:
    return f"{AUTO_JOB_PREFIX}:{chat_id}:{batch_id}"


def _preview_action_markup(batch_id: int, allowed_actions: set[str]) -> InlineKeyboardMarkup | None:
    """Backward-compatible keyboard wrapper for the shared presentation adapter."""
    ordered_actions = [
        action
        for action in ("IMPORT_HISTORICAL", "TRACK_ONLY", "DISMISS")
        if action in allowed_actions
    ]
    view = build_batch_summary(
        {},
        allowed_actions=ordered_actions,
        callback_data_factory=lambda action: f"{PREVIEW_ACTION_PREFIX}:{batch_id}:{action}",
    )
    if view.reply_markup is None:
        return None
    buttons = [button for row in view.reply_markup.inline_keyboard for button in row]
    return InlineKeyboardMarkup([buttons]) if buttons else None


async def _safe_edit_historical_message(
    bot,
    *,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup=None,
) -> bool:
    """Reuse the live parser's Telegram edit/fallback behavior without a module cycle."""
    from capitalguard.interfaces.telegram.forward_parsing_handler import smart_safe_edit

    return await smart_safe_edit(
        bot,
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        reply_markup=reply_markup,
    )


async def _finalize_auto_batch_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data or {}
    batch_id = data.get("batch_id")
    chat_id = data.get("chat_id")
    if not batch_id or not chat_id:
        return
    try:
        with session_scope() as session:
            preview = FrictionlessIngestionService().preview(session, batch_id=batch_id)
            parser = HistoricalParserService(ParsingService(ParsingRepository))
            parsed_count = 0
            partial_count = 0
            parsed_assets = []
            extracted_items = []
            parsed_results = []
            temporal_modes = Counter()
            temporal_routes = Counter()
            temporal_reasons = Counter()
            temporal_ages = []
            financial_outcome_status = Counter()
            financial_outcome_warnings = Counter()
            replay_gate_status = Counter()
            replay_gate_reasons = Counter()
            review_actions_by_mode = {}
            replay_gate = HistoricalReplayGateService()
            live_review = LiveReviewService()
            for record in preview.manifest.get("records", []):
                record_metadata = record.get("metadata") or {}
                temporal_decision = record_metadata.get("temporal_decision") or {}
                if temporal_decision.get("mode"):
                    temporal_modes[temporal_decision["mode"]] += 1
                if temporal_decision.get("route"):
                    temporal_routes[temporal_decision["route"]] += 1
                if temporal_decision.get("mode"):
                    review_actions_by_mode[temporal_decision["mode"]] = list(
                        live_review.prepare(temporal_decision).allowed_actions
                    )
                temporal_reasons.update(temporal_decision.get("reason_codes") or [])
                if temporal_decision.get("age_seconds") is not None:
                    temporal_ages.append(temporal_decision["age_seconds"])
                parsed = parser.parse(record.get("raw_text"))
                parsed_data = dict(parsed.data or {}) if parsed.parse_status == "PARSED" else {}
                semantic_projection = record_metadata.get("semantic_projection") or {}
                projection_canonical = semantic_projection.get("canonical") or {}
                semantic_status = str(semantic_projection.get("status") or "").upper()
                materialized_data = {
                    "asset": projection_canonical.get("asset"),
                    "side": projection_canonical.get("direction", projection_canonical.get("side")),
                    "entry": projection_canonical.get("entry"),
                    "stop_loss": projection_canonical.get("stop_loss"),
                    "targets": projection_canonical.get("targets") or [],
                    "market": projection_canonical.get("market"),
                }
                if semantic_status == "SUCCESS":
                    parsed_data = {**materialized_data, **parsed_data}
                elif not parsed_data and projection_canonical:
                    parsed_data = {**materialized_data, "semantic_status": semantic_status}
                displayable = bool(parsed_data) and any(
                    parsed_data.get(field) not in (None, "", [])
                    for field in ("asset", "side", "entry", "stop_loss", "targets")
                )
                is_complete = parsed.parse_status == "PARSED" or semantic_status == "SUCCESS"
                if is_complete:
                    parsed_count += 1
                else:
                    partial_count += 1
                outcome = parsed_data.get("financial_outcome") or {}
                if outcome.get("status"):
                    financial_outcome_status[outcome["status"]] += 1
                financial_outcome_warnings.update(outcome.get("warnings") or [])
                gate = replay_gate.assess(
                    parse_status=parsed.parse_status,
                    financial_outcome=outcome,
                    market_data_available=False,
                )
                replay_gate_status[gate.status] += 1
                replay_gate_reasons.update(gate.reason_codes)
                asset = parsed_data.get("asset")
                display_result = (parsed_data, record, semantic_status or parsed.parse_status)
                parsed_results.append(display_result)
                if displayable:
                    if asset:
                        parsed_assets.append(str(asset))
                    extracted_item = dict(parsed_data)
                    extracted_item["_receipt_id"] = record_metadata.get("forwarding_receipt_id")
                    extracted_item["_semantic_status"] = semantic_projection.get("status")
                    extracted_items.append(extracted_item)
            forwarding_service = HistoricalForwardingService()
            auto_progression = forwarding_service.auto_progress_canonical_batch(
                session,
                batch_id=batch_id,
            )
            auto_by_receipt = {
                int(item["receipt_id"]): item
                for item in (auto_progression.get("items") or [])
                if item.get("receipt_id") is not None
            }
            for item in extracted_items:
                replay = auto_by_receipt.get(item.get("_receipt_id"))
                if replay:
                    item["_replay"] = replay
            batch = session.get(HistoricalImportBatch, batch_id)
            metadata = dict(batch.metadata_json or {}) if batch else {}
            metadata["parser_preview"] = {
                "parsed_count": parsed_count,
                "partial_count": partial_count,
                "assets": parsed_assets,
                "replay_status": (
                    "REPLAYED" if auto_progression.get("progressed") and not auto_progression.get("failed") else
                    "REPLAY_PENDING" if auto_progression.get("review_required") else
                    "REPLAY_FAILED" if auto_progression.get("failed") else
                    "REPLAY_PENDING" if parsed_count else "NOT_PARSED"
                ),
                "auto_progression": auto_progression,
                "financial_outcome_status": dict(financial_outcome_status),
                "financial_outcome_warnings": dict(financial_outcome_warnings),
                "replay_gate_status": dict(replay_gate_status),
                "replay_gate_reasons": dict(replay_gate_reasons),
                "review_actions_by_mode": review_actions_by_mode,
            }
            metadata["temporal_summary"] = {
                "modes": dict(temporal_modes),
                "routes": dict(temporal_routes),
                "reason_codes": dict(temporal_reasons),
                "min_age_seconds": min(temporal_ages) if temporal_ages else None,
                "max_age_seconds": max(temporal_ages) if temporal_ages else None,
            }
            metadata["intake_status"] = "DRY_RUN"
            metadata["auto_batch_finalized"] = True
            if batch:
                batch.metadata_json = metadata
            # The user-facing historical result is informational and direct. Core
            # progression already runs in the background; owner commands must not
            # leak into this card or block the user's result.
            allowed_actions = {"DISMISS"}
            source_label = metadata.get("source_title") or "المصدر"
            summary_data = {
                "source_title": source_label,
                "total_records": preview.total_records,
                "processed_records": preview.total_records,
                "complete_records": parsed_count,
                "incomplete_records": partial_count,
                "unavailable_records": preview.rejected_records,
                "duplicate_records": preview.duplicate_records,
            }
            callback_factory = lambda action: f"{PREVIEW_ACTION_PREFIX}:{batch_id}:{action}"
            if preview.total_records == 1 and len(parsed_results) == 1:
                single_data, single_record, single_status = parsed_results[0]
                single_metadata = single_record.get("metadata") or {}
                temporal_decision = single_metadata.get("temporal_decision") or {}
                replay_result = auto_by_receipt.get(single_metadata.get("forwarding_receipt_id"))
                summary_view = build_single_result_card(
                    single_data,
                    temporal_route=temporal_decision.get("route"),
                    source_timestamp=single_metadata.get("source_message_timestamp") or single_metadata.get("source_timestamp"),
                    source_title=single_metadata.get("source_title") or source_label,
                    internal_status=single_status,
                    substatus=single_data.get("semantic_status"),
                    financial_outcome=single_data.get("financial_outcome"),
                    replay_result=replay_result,
                    allowed_actions=allowed_actions,
                    callback_data_factory=callback_factory,
                )
            elif preview.total_records == 1:
                summary_view = build_single_result_card(
                    {},
                    temporal_route="QUARANTINE",
                    source_timestamp=None,
                    source_title=source_label,
                    internal_status="FAILED",
                    allowed_actions=allowed_actions,
                    callback_data_factory=callback_factory,
                )
            else:
                summary_view = build_batch_summary(
                    summary_data,
                    extracted_items=extracted_items,
                    allowed_actions=allowed_actions,
                    callback_data_factory=callback_factory,
                )
            text = summary_view.text
            markup = summary_view.reply_markup
            progress_message_id = context.user_data.get(AUTO_PROGRESS_MESSAGE_KEY)
        if progress_message_id:
            edited = await _safe_edit_historical_message(
                context.bot,
                chat_id=chat_id,
                message_id=progress_message_id,
                text=text,
                reply_markup=markup,
            )
        else:
            edited = False
        if not edited:
            await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
    except Exception:
        log.exception("Automatic historical batch finalization failed for batch %s", batch_id)
        error_text = "⚠️ تعذر تجهيز النتيجة الآن. تم حفظ الرسالة ويمكن إعادة المحاولة لاحقًا."
        progress_message_id = context.user_data.get(AUTO_PROGRESS_MESSAGE_KEY)
        if progress_message_id:
            edited = await _safe_edit_historical_message(
                context.bot,
                chat_id=chat_id,
                message_id=progress_message_id,
                text=error_text,
                reply_markup=None,
            )
        else:
            edited = False
        if not edited:
            await context.bot.send_message(chat_id=chat_id, text=error_text)
    finally:
        if context.user_data.get(AUTO_BATCH_KEY) == batch_id:
            context.user_data.pop(AUTO_BATCH_KEY, None)
        context.user_data.pop(AUTO_PROGRESS_MESSAGE_KEY, None)


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
    forwarded_input = _forwarded_input(message, user_id=db_user.id, details=details)
    parsed_payload = None
    current_price = None
    market_data_available = False
    market_snapshot_time = None
    if forwarded_input.raw_text:
        parser = HistoricalParserService(ParsingService(ParsingRepository))
        parsed = parser.parse(forwarded_input.raw_text)
        parsed_payload = parsed.data or {}
        source_time = forwarded_input.source_message_timestamp
        age_seconds = (
            max(0, int((datetime.now(timezone.utc) - source_time).total_seconds()))
            if source_time
            else None
        )
        if parsed.parse_status == "PARSED" and parsed_payload.get("asset") and age_seconds is not None and age_seconds <= 180:
            current_price = await PriceService().get_cached_price(
                str(parsed_payload["asset"]),
                "Futures",
                force_refresh=True,
            )
            market_data_available = current_price is not None
            market_snapshot_time = datetime.now(timezone.utc)
    receipt = service.stage_direct_message(
        db_session,
        batch_id=batch.id,
        message=forwarded_input,
        parsed_payload=parsed_payload,
        current_price=current_price,
        market_data_available=market_data_available,
        market_snapshot_time=market_snapshot_time,
    )
    await _materialize_historical_content(db_session, db_user, receipt)
    context.user_data[AUTO_BATCH_KEY] = batch.id

    if not context.user_data.get(AUTO_PROGRESS_MESSAGE_KEY):
        progress_message = await message.reply_text("📥 جارٍ استخراج القيم وتجهيز النتيجة…")
        context.user_data[AUTO_PROGRESS_MESSAGE_KEY] = progress_message.message_id

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


@uow_transaction
@require_active_user
async def historical_preview_decision_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    if not query or not db_user or not query.data:
        return
    try:
        _, batch_text, action = query.data.split(":", 2)
        batch_id = int(batch_text)
        batch = HistoricalForwardingService().apply_preview_decision(
            db_session,
            batch_id=batch_id,
            requested_by_user_id=db_user.id,
            action=action,
        )
        await query.answer()
        messages = {
            "IMPORT_HISTORICAL": "✅ تم إرسال الدفعة إلى طابور Owner Review. لا يزال Evidence وReplay خطوتين منفصلتين.",
            "TRACK_ONLY": "✅ تم حفظ الدفعة كتتبع فقط. لم تُنشأ توصية أو صفقة أو عملية Replay.",
            "DISMISS": "✅ تم تجاهل الدفعة. لم تُنشأ توصية أو صفقة أو دليل تاريخي.",
        }
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"{messages[action]}\nbatch_id={batch.id}\nstatus={batch.status}")
    except (ValueError, HistoricalSignalValidationError) as exc:
        await query.answer("تعذر تطبيق القرار", show_alert=True)
        await query.message.reply_text(f"⚠️ Historical decision rejected: {exc}")


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
    photos = list(getattr(message, "photo", None) or [])
    largest_photo = photos[-1] if photos else None
    media = {
        "media_type": "PHOTO",
        "file_id": getattr(largest_photo, "file_id", None),
        "media_unique_id": getattr(largest_photo, "file_unique_id", None),
        "width": getattr(largest_photo, "width", None),
        "height": getattr(largest_photo, "height", None),
    } if largest_photo is not None else None
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
                "media": media,
            },
        ),
    )
    await _materialize_historical_content(db_session, db_user, receipt)
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


@uow_transaction
@require_active_user
async def historical_forward_review_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Approve or reject a dry-run batch; approval never creates live entities."""
    if not update.message:
        return
    if not _is_admin(update.effective_chat.id):
        await update.message.reply_text("🚫 Historical batch review is restricted to administration.")
        return
    if len(context.args or []) < 2:
        await update.message.reply_text("Usage: /historical_forward_review <batch_id> <approve|reject> [note]")
        return
    try:
        batch_id = int(context.args[0])
        decision = context.args[1].strip().lower()
        if decision not in {"approve", "reject"}:
            raise HistoricalOwnerReviewError("decision must be approve or reject")
        note = " ".join(context.args[2:]).strip() or None
        batch = HistoricalOwnerReviewService().review_batch(
            db_session,
            batch_id=batch_id,
            reviewer_user_id=db_user.id,
            approved=decision == "approve",
            note=note,
        )
        await update.message.reply_text(
            f"✅ Historical batch review recorded\\n"
            f"batch_id={batch.id}\\n"
            f"status={batch.status}\\n"
            "Evidence ingestion remains a separate controlled step."
        )
    except (ValueError, HistoricalOwnerReviewError) as exc:
        await update.message.reply_text(f"⚠️ Historical review rejected: {exc}")


@uow_transaction
@require_active_user
async def historical_forward_ingest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Ingest reviewed receipts as immutable evidence; never creates live entities."""
    if not update.message:
        return
    if not _is_admin(update.effective_chat.id):
        await update.message.reply_text("🚫 Evidence ingestion is restricted to administration.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /historical_forward_ingest <batch_id>")
        return
    try:
        batch_id = int(context.args[0])
        ingested, skipped = HistoricalEvidenceIngestionService().ingest_reviewed_batch(
            db_session,
            batch_id=batch_id,
            reviewer_user_id=db_user.id,
        )
        await update.message.reply_text(
            "✅ Historical evidence ingestion completed\\n"
            f"batch_id={batch_id}\\n"
            f"ingested={ingested}\\n"
            f"skipped={skipped}\\n"
            "No live recommendation or trader position was created."
        )
    except (ValueError, HistoricalEvidenceIngestionError) as exc:
        await update.message.reply_text(f"⚠️ Evidence ingestion rejected: {exc}")


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
    application.add_handler(CommandHandler("historical_forward_review", historical_forward_review_cmd), group=0)
    application.add_handler(CommandHandler("historical_forward_ingest", historical_forward_ingest_cmd), group=0)
    application.add_handler(CallbackQueryHandler(historical_preview_decision_callback, pattern=rf"^{PREVIEW_ACTION_PREFIX}:"), group=0)
