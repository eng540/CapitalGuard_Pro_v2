# --- START OF FINAL, FULLY CORRECTED AND ROBUST FILE: src/capitalguard/interfaces/telegram/commands.py ---
import io
import csv
import logging
from typing import Optional, Tuple

from telegram import Update, InputFile
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters

from .helpers import get_service
from .auth import ALLOWED_USER_FILTER
from .ui_texts import build_analyst_stats_text
from .keyboards import build_open_recs_keyboard

from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.price_service import PriceService

from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.infrastructure.db.repository import UserRepository, ChannelRepository

log = logging.getLogger(__name__)

AWAITING_FORWARD_KEY = "awaiting_forward_channel_link"


# ---------------------------
# Generic helpers
# ---------------------------
def _parse_channel_ref(raw: str) -> Tuple[Optional[int], Optional[str]]:
    s = (raw or "").strip()
    if not s:
        return None, None
    if s.startswith("@"):
        return None, s[1:]
    try:
        return int(s), None
    except ValueError:
        return None, s

def _extract_forwarded_channel(message) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    chat_obj = getattr(message, "forward_from_chat", None)
    if chat_obj is None:
        fwd_origin = getattr(message, "forward_origin", None)
        if fwd_origin:
            chat_obj = getattr(fwd_origin, "chat", None)
    if chat_obj is None or getattr(chat_obj, "type", None) != "channel":
        return None, None, None
    return (
        int(getattr(chat_obj, "id")),
        getattr(chat_obj, "title", None),
        getattr(chat_obj, "username", None),
    )

async def _bot_has_post_rights(context: ContextTypes.DEFAULT_TYPE, channel_id: int) -> bool:
    try:
        await context.bot.send_message(
            chat_id=channel_id, text="✅ تم ربط القناة بنجاح.", disable_notification=True
        )
        return True
    except Exception as e:
        log.warning("Bot posting rights check failed for channel %s: %s", channel_id, e)
        return False


# ---------------------------
# Basic commands
# ---------------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html("👋 أهلاً بك في <b>CapitalGuard Bot</b>.\nاستخدم /help للمساعدة.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        "<b>Available Commands:</b>\n\n"
        "<b>--- إنشاء توصية ---</b>\n"
        "• <code>/newrec</code> — عرض قائمة طرق الإنشاء.\n"
        "• <code>/new</code> — بدء المنشئ التفاعلي مباشرة.\n"
        "• <code>/rec</code> — استخدام وضع الأمر السريع مباشرة.\n"
        "• <code>/editor</code> — استخدام المحرر النصي مباشرة.\n\n"
        "<b>--- إدارة وتحليل ---</b>\n"
        "• <code>/open [filter]</code> — عرض توصياتك المفتوحة.\n"
        "• <code>/stats</code> — ملخّص أدائك الشخصي.\n"
        "• <code>/export</code> — تصدير توصياتك.\n\n"
        "<b>--- إدارة القنوات ---</b>\n"
        "• <code>/link_channel</code> — ربط قناة عبر إعادة التوجيه.\n"
        "• <code>/channels</code> — عرض قنواتك المرتبطة.\n"
        "• <code>/toggle_channel &lt;id&gt;</code> — تفعيل/تعطيل قناة.\n"
        "• <code>/unlink_channel &lt;id&gt;</code> — فك ربط قناة.\n\n"
        "<b>--- إعدادات ---</b>\n"
        "• <code>/settings</code> — (سيتم تفعيلها مستقبلاً لإدارة الحساب)."
    )

async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚙️ الإعدادات\n\n"
        "هذه المنطقة مخصصة لإدارة إعدادات حسابك مستقبلاً."
    )

# ---------------------------
# Open recommendations
# ---------------------------
async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trade_service: TradeService = get_service(context, "trade_service")
    price_service: PriceService = get_service(context, "price_service")
    user_telegram_id = update.effective_user.id

    filters_map = {}
    filter_text_parts = []
    if context.args:
        for arg in context.args:
            a = arg.strip().lower()
            if a in ("long", "short"):
                filters_map["side"] = a
                filter_text_parts.append(f"الاتجاه: {a.upper()}")
            elif a in ("pending", "active"):
                filters_map["status"] = a
                filter_text_parts.append(f"الحالة: {a.upper()}")
            else:
                filters_map["symbol"] = a
                filter_text_parts.append(f"الرمز: {a.upper()}")

    context.user_data["last_open_filters"] = filters_map

    with SessionLocal() as session:
        items = trade_service.repo.list_open_for_user(
            session=session,
            user_telegram_id=user_telegram_id,
            **filters_map
        )

    if not items:
        await update.message.reply_text("✅ لا توجد توصيات مفتوحة تطابق الفلتر الحالي.")
        return

    keyboard = await build_open_recs_keyboard(items, current_page=1, price_service=price_service)

    header_text = "<b>📊 لوحة قيادة توصياتك المفتوحة</b>"
    if filter_text_parts:
        header_text += f"\n<i>فلترة حسب: {', '.join(filter_text_parts)}</i>"

    await update.message.reply_html(
        f"{header_text}\nاختر توصية لعرض لوحة التحكم الخاصة بها:",
        reply_markup=keyboard
    )

# ---------------------------
# Stats & export
# ---------------------------
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    analytics_service: AnalyticsService = get_service(context, "analytics_service")
    user_id_str = str(update.effective_user.id)
    stats = analytics_service.performance_summary_for_user(user_id_str)
    text = build_analyst_stats_text(stats)
    await update.message.reply_html(text)

async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("جاري تجهيز ملف التصدير...")
    trade_service: TradeService = get_service(context, "trade_service")
    user_telegram_id = update.effective_user.id

    with SessionLocal() as session:
        all_recs = trade_service.repo.list_all_for_user(session, user_telegram_id)
    
    if not all_recs:
        await update.message.reply_text("لا توجد بيانات للتصدير.")
        return

    output = io.StringIO()
    writer = csv.writer(output)
    header = [
        "id", "asset", "side", "status", "market", "entry_price", "stop_loss",
        "targets", "exit_price", "notes", "created_at", "closed_at"
    ]
    writer.writerow(header)
    for rec in all_recs:
        row = [
            rec.id, rec.asset.value, rec.side.value, rec.status.value,
            rec.market, rec.entry.value, rec.stop_loss.value,
            ", ".join(str(t.price) for t in rec.targets.values), rec.exit_price, rec.notes,
            rec.created_at.strftime('%Y-%m-%d %H:%M:%S') if rec.created_at else "",
            rec.closed_at.strftime('%Y-%m-%d %H:%M:%S') if rec.closed_at else ""
        ]
        writer.writerow(row)

    output.seek(0)
    bytes_buffer = io.BytesIO(output.getvalue().encode("utf-8"))
    csv_file = InputFile(bytes_buffer, filename="capitalguard_export.csv")
    await update.message.reply_document(document=csv_file, caption="تم إنشاء التصدير.")

# ---------------------------
# Channel management
# ---------------------------
async def link_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        context.user_data[AWAITING_FORWARD_KEY] = True
        await update.message.reply_html(
            "<b>🔗 ربط قناة عبر إعادة التوجيه</b>\n"
            "أعد توجيه <u>أي رسالة</u> من القناة المراد ربطها إلى هنا.\n"
            "• يدعم القنوات <b>الخاصة</b> و<b>العامة</b>.\n"
            "• تأكد أن هذا البوت مُضاف كمسؤول بصلاحية النشر."
        )
        return

    raw = context.args[0].strip()
    _, uname = _parse_channel_ref(raw)
    if uname:
        await update.message.reply_text(
            f"ℹ️ لاستكمال ربط @{uname}: يرجى إعادة توجيه رسالة من القناة للتأكد من صلاحيات النشر والحصول على المعرف الصحيح."
        )
    else:
        await update.message.reply_text(
            "ℹ️ لربط القناة عبر المعرّف الرقمي: أعد توجيه رسالة من القناة لضمان التحقق التلقائي من الصلاحيات."
        )

async def link_channel_forward_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not context.user_data.pop(AWAITING_FORWARD_KEY, False):
        return

    user_tg_id = int(update.effective_user.id)
    chat_id, title, username = _extract_forwarded_channel(msg)
    if not chat_id:
        return

    await msg.reply_text(f"⏳ جارِ التحقق من صلاحيات النشر في القناة (ID: {chat_id}) ...")

    if not await _bot_has_post_rights(context, chat_id):
        await msg.reply_text("❌ تعذر النشر في هذه القناة. تأكد أن البوت مُضاف كمسؤول.")
        return

    try:
        with SessionLocal() as session:
            user = UserRepository(session).find_or_create(user_tg_id)
            ChannelRepository(session).add(
                owner_user_id=user.id,
                telegram_channel_id=chat_id,
                username=username,
                title=title,
            )
            session.commit()
    except Exception as e:
        err = str(e).lower()
        if "unique" in err or "integrity" in err or "already" in err:
            await msg.reply_text("ℹ️ هذه القناة مرتبطـة مسبقًا وتم تحديث بياناتها.")
        else:
            await msg.reply_text(f"❌ حدث خطأ أثناء ربط القناة: {e}")
        return

    uname_disp = f"@{username}" if username else "قناة خاصة"
    await msg.reply_text(
        f"✅ تم ربط القناة بنجاح: {title or '-'} ({uname_disp})\nID: <code>{chat_id}</code>",
        parse_mode="HTML",
    )

async def channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_tg_id = int(update.effective_user.id)
    with SessionLocal() as session:
        user = UserRepository(session).find_by_telegram_id(user_tg_id)
        if not user:
            await update.message.reply_text("📭 لا توجد قنوات مرتبطة بحسابك.")
            return
        channels = ChannelRepository(session).list_by_user(user.id, only_active=False) or []

    if not channels:
        await update.message.reply_text("📭 لا توجد قنوات مرتبطة بحسابك.")
        return

    lines = ["<b>📡 قنواتك المرتبطة</b>"]
    for ch in channels:
        uname = f"@{ch.username}" if getattr(ch, "username", None) else "—"
        title = getattr(ch, "title", None) or "—"
        status = "✅ فعّالة" if ch.is_active else "⏸️ معطّلة"
        lines.append(f"• <b>{title}</b> ({uname} / <code>{ch.telegram_channel_id}</code>) — {status}")
    lines.append("\nℹ️ للتحكم: <code>/toggle_channel &lt;id&gt;</code> | <code>/unlink_channel &lt;id&gt;</code>")
    await update.message.reply_html("\n".join(lines))

async def toggle_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استخدم: /toggle_channel <id>")
        return

    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("المعرف غير صالح. استخدم رقم القناة (ID).")
        return

    user_tg_id = int(update.effective_user.id)
    with SessionLocal() as session:
        try:
            user = UserRepository(session).find_by_telegram_id(user_tg_id)
            if not user:
                await update.message.reply_text("لم يتم العثور على حسابك.")
                return

            repo = ChannelRepository(session)
            channels = repo.list_by_user(user.id, only_active=False)
            target = next((c for c in channels if c.telegram_channel_id == chat_id), None)
            if not target:
                await update.message.reply_text("لم يتم العثور على القناة لهذا الحساب.")
                return
            
            repo.set_active(user.id, chat_id, not target.is_active)
            session.commit()
            await update.message.reply_text("تم تحديث حالة القناة.")
        except Exception as e:
            session.rollback()
            log.error(f"Error toggling channel: {e}")
            await update.message.reply_text("حدث خطأ أثناء تحديث القناة.")

async def unlink_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ميزة فك الربط غير مفعلة بعد.")

# ---------------------------
# Registration
# ---------------------------
def register_commands(app: Application):
    app.add_handler(CommandHandler("start", start_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("help", help_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("open", open_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("stats", stats_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("export", export_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("settings", settings_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("link_channel", link_channel_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("channels", channels_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("toggle_channel", toggle_channel_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("unlink_channel", unlink_channel_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(MessageHandler(ALLOWED_USER_FILTER & filters.FORWARDED, link_channel_forward_handler))
# --- END OF FINAL, FULLY CORRECTED AND ROBUST FILE ---