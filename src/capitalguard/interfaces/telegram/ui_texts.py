# --- START OF FILE: src/capitalguard/interfaces/telegram/ui_texts.py ---
from __future__ import annotations
from typing import Iterable, List, Optional, Dict, Any
from math import isfinite
from datetime import datetime, timezone

# --- (Helper functions _pct, _format_targets, _rr, _rr_actual remain unchanged) ---
def _pct(entry: float, target: float, side: str) -> float:
    if not entry or entry == 0: return 0.0
    return ((target - entry) / entry * 100.0) if (side or "").upper() == "LONG" else ((entry - target) / entry * 100.0)
def _format_targets(entry: float, side: str, tps: Iterable[float]) -> str:
    lines: List[str] = []
    for i, tp in enumerate(tps, start=1):
        try:
            pct = _pct(entry, float(tp), side)
            lines.append(f"• TP{i}: {float(tp):g} ({pct:+.2f}%)")
        except (ValueError, TypeError): continue
    return "\n".join(lines) if lines else "—"
def _rr(entry: float, sl: float, tp1: Optional[float], side: str) -> str:
    try:
        risk = abs(entry - sl);
        if risk <= 0 or tp1 is None: return "—"
        reward = abs(tp1 - entry) if side.upper() == "LONG" else abs(entry - tp1)
        ratio = reward / risk; return f"{ratio:.2f}" if isfinite(ratio) else "—"
    except Exception: return "—"
def _rr_actual(entry: float, sl: float, exit_price: Optional[float], side: str) -> str:
    try:
        if exit_price is None: return "—"
        risk = abs(entry - sl);
        if risk <= 0: return "—"
        reward = abs(exit_price - entry) if side.upper() == "LONG" else abs(entry - exit_price)
        ratio = reward / risk; return f"{ratio:.2f}" if isfinite(ratio) else "—"
    except Exception: return "—"

# --- (build_trade_card_text, build_review_text, build_review_text_with_price remain unchanged) ---
def build_trade_card_text(rec) -> str:
    rec_id = getattr(rec, "id", None)
    asset = getattr(getattr(rec, "asset", None), "value", getattr(rec, "asset", "N/A"))
    side = getattr(getattr(rec, "side", None), "value", getattr(rec, "side", "N/A"))
    entry = float(getattr(getattr(rec, "entry", None), "value", getattr(rec, "entry", 0)))
    sl = float(getattr(getattr(rec, "stop_loss", None), "value", getattr(rec, "stop_loss", 0)))
    tps = list(getattr(getattr(rec, "targets", None), "values", getattr(rec, "targets", [])))
    tp1 = float(tps[0]) if tps else None
    notes = getattr(rec, "notes", None) or "—"
    status = str(getattr(rec, "status", "OPEN")).upper()
    title_line = f"<b>{asset}</b> — {side}"
    if rec_id: title_line = f"Signal #{rec_id} | <b>{asset}</b> — {side}"
    if status == "CLOSED":
        exit_p = getattr(rec, 'exit_price', None)
        rr_act = _rr_actual(entry, sl, float(exit_p or 0), side)
        status_line = f"✅ <b>CLOSED</b> at {exit_p:g} (R/R act: {rr_act})"
    else: status_line = f"🟢 <b>OPEN</b>"
    live_price = getattr(rec, "live_price", None)
    live_price_line = ""
    if live_price and status == 'OPEN':
        pnl = _pct(entry, live_price, side)
        now_utc = datetime.now(timezone.utc).strftime('%H:%M %Z')
        live_price_line = f"<i>Live Price ({now_utc}): {live_price:g} (PnL: {pnl:+.2f}%)</i>\n"
    planned_rr = _rr(entry, sl, tp1, side)
    return (f"{title_line}\n"
            f"Status: {status_line}\n"
            f"{live_price_line}\n"
            f"Entry 💰: {entry:g}\n"
            f"SL 🛑: {sl:g}\n"
            f"<u>Targets</u>:\n{_format_targets(entry, side, tps)}\n\n"
            f"R/R (plan): <b>{planned_rr}</b>\n"
            f"Notes: <i>{notes}</i>\n\n"
            f"#{asset} #Signal #{side}")
def build_review_text(draft: dict) -> str:
    asset = (draft.get("asset","") or "").upper()
    side = (draft.get("side","") or "").upper()
    market = (draft.get("market","") or "-")
    entry = float(draft.get("entry",0) or 0)
    sl = float(draft.get("stop_loss",0) or 0)
    raw = draft.get("targets")
    if isinstance(raw, str): raw = [x for x in raw.replace(",", " ").split() if x]
    tps: List[float] = []
    for x in (raw or []):
        try: tps.append(float(x))
        except: pass
    tp1 = float(tps[0]) if tps else None
    planned_rr = _rr(entry, sl, tp1, side)
    notes = draft.get("notes") or "-"
    lines_tps = "\n".join([f"• TP{i}: {tp:g}" for i,tp in enumerate(tps, start=1)]) or "—"
    return ("📝 <b>مراجعة التوصية</b>\n\n"
            f"<b>{asset}</b> | {market} / {side}\n"
            f"Entry 💰: {entry:g}\n"
            f"SL 🛑: {sl:g}\n"
            f"<u>Targets</u>:\n{lines_tps}\n\n"
            f"R/R (plan): <b>{planned_rr}</b>\n"
            f"ملاحظات: <i>{notes}</i>\n\n"
            "هل تريد نشر هذه التوصية في القناة؟")
def build_review_text_with_price(draft: dict, preview_price: float | None) -> str:
    base = build_review_text(draft)
    if preview_price is None: return base + "\n\n🔎 Current Price: —"
    return base + f"\n\n🔎 Current Price: <b>{preview_price:g}</b>"

# ✅ --- NEW FUNCTION FOR THE /STATS COMMAND ---
def build_analyst_stats_text(stats: Dict[str, Any]) -> str:
    """Formats the analyst's performance statistics into a readable message."""
    
    # Safely get values from the stats dictionary
    total = stats.get('total_recommendations', 0)
    open_recs = stats.get('open_recommendations', 0)
    closed_recs = stats.get('closed_recommendations', 0)
    win_rate = stats.get('overall_win_rate', '0.00%')
    total_pnl = stats.get('total_pnl_percent', '0.00%')

    # Build the text lines
    lines = [
        "📊 <b>Your Performance Summary</b> 📊",
        "─" * 15,
        f"Total Recommendations: <b>{total}</b>",
        f"Open Trades: <b>{open_recs}</b>",
        f"Closed Trades: <b>{closed_recs}</b>",
        "─" * 15,
        f"Overall Win Rate: <b>{win_rate}</b>",
        f"Total PnL (Cumulative %): <b>{total_pnl}</b>",
        "─" * 15,
        f"<i>Report generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
    ]
    return "\n".join(lines)
# --- END OF FILE ---```

---

#### **3. تحديث `commands.py` لإضافة الأوامر الجديدة**
*   **السبب:** إضافة الأوامر الجديدة (`/stats`, `/export`) التي يمكن للمحلل استخدامها.
*   **القرار الهندسي:** سنضيف هذه الأوامر إلى ملف `commands.py` الحالي للحفاظ على تنظيم الكود.

**ملف معدل:** `src/capitalguard/interfaces/telegram/commands.py` (استبدال كامل)
```python
# --- START OF FILE: src/capitalguard/interfaces/telegram/commands.py ---
import io
import csv
from telegram import Update, InputFile
from telegram.ext import Application, ContextTypes, CommandHandler
from .helpers import get_service
from .keyboards import recommendation_management_keyboard
from .auth import ALLOWED_FILTER
from .ui_texts import build_analyst_stats_text
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html("👋 Welcome to the <b>CapitalGuard Bot</b>.\nUse /help for assistance.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        "<b>Available Commands:</b>\n\n"
        "• <code>/newrec</code> — Start a conversation to create a recommendation.\n"
        "• <code>/open</code> — View and manage open recommendations.\n"
        "• <code>/stats</code> — View your performance summary.\n"
        "• <code>/export</code> — Export all your recommendations as a CSV file."
    )

async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trade_service: TradeService = get_service(context, "trade_service")
    # In a single-analyst setup, we don't need to filter by user_id yet.
    items = trade_service.list_open()
    if not items:
        await update.message.reply_text("There are no open recommendations.")
        return
    
    await update.message.reply_text("Here are your open recommendations:")
    for it in items:
        text = (f"<b>#{it.id}</b> — <b>{it.asset.value}</b> ({it.side.value}) | Status: {it.status}")
        # Note: The control panel is now sent privately upon creation.
        # This command is just for listing them.
        await update.message.reply_html(text)

# ✅ --- NEW COMMAND HANDLERS ---

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a summary of the analyst's performance."""
    analytics_service: AnalyticsService = get_service(context, "analytics_service")
    # In a single-analyst setup, all stats belong to the one user.
    stats = analytics_service.performance_summary()
    text = build_analyst_stats_text(stats)
    await update.message.reply_html(text)

async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exports all recommendation data to a CSV file."""
    await update.message.reply_text("Generating your data export, this may take a moment...")
    
    trade_service: TradeService = get_service(context, "trade_service")
    all_recs = trade_service.list_all()

    if not all_recs:
        await update.message.reply_text("No recommendations found to export.")
        return

    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    header = [
        "id", "asset", "side", "status", "market", "entry_price", "stop_loss", 
        "targets", "exit_price", "notes", "created_at", "closed_at"
    ]
    writer.writerow(header)
    
    # Write data rows
    for rec in all_recs:
        row = [
            rec.id,
            rec.asset.value,
            rec.side.value,
            rec.status,
            rec.market,
            rec.entry.value,
            rec.stop_loss.value,
            ", ".join(map(str, rec.targets.values)),
            rec.exit_price,
            rec.notes,
            rec.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            rec.closed_at.strftime('%Y-%m-%d %H:%M:%S') if rec.closed_at else ""
        ]
        writer.writerow(row)
        
    output.seek(0)
    # Create a bytes buffer to send the file
    bytes_buffer = io.BytesIO(output.getvalue().encode('utf-8'))
    
    # Create an InputFile object
    csv_file = InputFile(bytes_buffer, filename="capitalguard_export.csv")
    
    await update.message.reply_document(document=csv_file, caption="Here is your data export.")

def register_commands(app: Application):
    app.add_handler(CommandHandler("start", start_cmd, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("help", help_cmd, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("open", open_cmd, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("stats", stats_cmd, filters=ALLOWED_FILTER))
    app.add_handler(CommandHandler("export", export_cmd, filters=ALLOWED_FILTER))
# --- END OF FILE ---