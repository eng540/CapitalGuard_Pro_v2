# --- src/capitalguard/infrastructure/notify/telegram.py ---
# src/capitalguard/infrastructure/notify/telegram.py (v9.0 - Decoupled)
"""
Handles all outbound communication to the Telegram Bot API.
✅ HOTFIX: Decoupled from interfaces layer to break circular dependency.
- Moved `build_trade_card_text`, `public_channel_keyboard`, and all helper
  functions (like `_pct`, `_format_price`, `_get_attr`) directly into this file.
- This file is now self-contained for building and sending messages.
"""

import logging
from typing import Optional, Tuple, Dict, Any, Union, List
from decimal import Decimal, InvalidOperation
from datetime import datetime

import httpx
from telegram import InlineKeyboardMarkup, Bot, InlineKeyboardButton
from telegram.ext import Application

from capitalguard.config import settings
from capitalguard.domain.entities import Recommendation, RecommendationStatus
# ❌ REMOVED: from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
# ❌ REMOVED: from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text

log = logging.getLogger(__name__)

# ---------------------------
# Internal Message Building Helpers (Moved from ui_texts.py)
# ---------------------------

_STATUS_MAP = {
    RecommendationStatus.PENDING: "⏳ PENDING",
    RecommendationStatus.ACTIVE: "⚡️ ACTIVE",
    RecommendationStatus.CLOSED: "🏁 CLOSED",
}
_SIDE_ICONS = {'LONG': '🟢', 'SHORT': '🔴'}

def _get_attr(obj: Any, attr: str, default: Any = None) -> Any:
    """Safely get .attr if exists, else x itself (for domain ValueObjects)."""
    val = getattr(obj, attr, default)
    return val.value if hasattr(val, 'value') else val

def _to_decimal(value: Any, default: Decimal = Decimal('0')) -> Decimal:
    if isinstance(value, Decimal): return value if value.is_finite() else default
    if value is None: return default
    try: d = Decimal(str(value)); return d if d.is_finite() else default
    except (InvalidOperation, TypeError, ValueError): return default

def _format_price(price: Any) -> str:
    price_dec = _to_decimal(price)
    return "N/A" if not price_dec.is_finite() else f"{price_dec:g}"

def _pct(entry: Any, target_price: Any, side: str) -> float:
    entry_dec, target_dec = _to_decimal(entry), _to_decimal(target_price)
    if not entry_dec.is_finite() or entry_dec.is_zero() or not target_dec.is_finite(): return 0.0
    side_upper = (_get_attr(side, "value") or "").upper() # Use _get_attr
    try:
        if side_upper == "LONG": pnl = ((target_dec / entry_dec) - 1) * 100
        elif side_upper == "SHORT": pnl = ((entry_dec / target_dec) - 1) * 100
        else: return 0.0
        return float(pnl)
    except (InvalidOperation, TypeError, ZeroDivisionError): return 0.0

def _format_pnl(pnl: float) -> str:
    return f"{pnl:+.2f}%"

def _rr(entry: Any, sl: Any, first_target: Optional[Any]) -> str: # Use Any for Target VO
    try:
        entry_dec, sl_dec = _to_decimal(entry), _to_decimal(sl)
        if first_target is None or not entry_dec.is_finite() or not sl_dec.is_finite(): return "—"
        risk = abs(entry_dec - sl_dec)
        if risk.is_zero(): return "∞"
        reward = abs(_to_decimal(_get_attr(first_target, 'price')) - entry_dec)
        ratio = reward / risk
        return f"1:{ratio:.2f}"
    except Exception: return "—"

def _calculate_weighted_pnl(rec: Recommendation) -> float:
    total_pnl_contribution = 0.0
    total_percent_closed = 0.0
    closure_event_types = ("PARTIAL_CLOSE_MANUAL", "PARTIAL_CLOSE_AUTO", "FINAL_CLOSE") # Use FINAL_CLOSE

    if not rec.events:
        if rec.status == RecommendationStatus.CLOSED and rec.exit_price is not None:
            return _pct(rec.entry.value, rec.exit_price, rec.side.value)
        return 0.0

    for event in rec.events:
        event_type = getattr(event, "event_type", "")
        if event_type in closure_event_types:
            data = getattr(event, "event_data", {}) or {}
            closed_pct = data.get('closed_percent', 0.0)
            pnl_on_part = data.get('pnl_on_part', 0.0)
            
            if closed_pct > 0:
                total_pnl_contribution += (closed_pct / 100.0) * pnl_on_part
                total_percent_closed += closed_pct

    if total_percent_closed == 0 and rec.status == RecommendationStatus.CLOSED and rec.exit_price is not None:
        return _pct(rec.entry.value, rec.exit_price, rec.side.value)
      
    if 99.9 < total_percent_closed < 100.1: # Handle precision issues
        normalization_factor = 100.0 / total_percent_closed if total_percent_closed > 0 else 1.0
        return total_pnl_contribution * normalization_factor

    return total_pnl_contribution

def _get_result_text(pnl: float) -> str:
    if pnl > 0.001: return "🏆 WIN"
    elif pnl < -0.001: return "💔 LOSS"
    else: return "🛡️ BREAKEVEN"

def _build_header(rec: Recommendation) -> str:
    status_text = _STATUS_MAP.get(rec.status, "UNKNOWN")
    side_icon = _SIDE_ICONS.get(rec.side.value, '⚪')
    id_prefix = "Trade" if getattr(rec, 'is_user_trade', False) else "Signal"
    return f"<b>{status_text} | #{_get_attr(rec.asset, 'value')} | {_get_attr(rec.side, 'value')}</b> {side_icon} | {id_prefix} #{rec.id}"

def _build_live_price_section(rec: Recommendation) -> str:
    live_price = getattr(rec, "live_price", None)
    if rec.status != RecommendationStatus.ACTIVE or live_price is None: return ""
    pnl = _pct(rec.entry.value, live_price, rec.side.value)
    pnl_icon = '🟢' if pnl >= 0 else '🔴'
    return "\n".join([
        "─ ─ ─ ─ ─ ─ ─ ─ ─ ─",
        f"💹 <b>Live Price:</b> <code>{_format_price(live_price)}</code> ({pnl_icon} {_format_pnl(pnl)})",
        "─ ─ ─ ─ ─ ─ ─ ─ ─ ─"
    ])

def _build_performance_section(rec: Recommendation) -> str:
    entry_price, stop_loss = rec.entry.value, rec.stop_loss.value
    sl_pnl = _pct(entry_price, stop_loss, rec.side.value)
    first_target = rec.targets.values[0] if rec.targets.values else None
    return "\n".join([
        "📊 <b>PERFORMANCE</b>",
        f"💰 Entry: <code>{_format_price(entry_price)}</code>",
        f"🛑 Stop: <code>{_format_price(stop_loss)}</code> ({_format_pnl(sl_pnl)})",
        f"💡 Risk/Reward (Plan): ~<code>{_rr(entry_price, stop_loss, first_target)}</code>"
    ])

def _build_exit_plan_section(rec: Recommendation) -> str:
    lines = ["\n🎯 <b>EXIT PLAN</b>"]
    entry_price = rec.entry.value
    hit_targets = set()
    if rec.events:
        for event in rec.events:
            if event.event_type.startswith("TP") and event.event_type.endswith("_HIT"):
                try:
                    target_num = int(event.event_type[2:-4])
                    hit_targets.add(target_num)
                except (ValueError, IndexError):
                    continue
    next_tp_index = -1
    for i in range(1, len(rec.targets.values) + 1):
        if i not in hit_targets:
            next_tp_index = i
            break
    for i, target in enumerate(rec.targets.values, start=1):
        pct_value = _pct(entry_price, target.price.value, rec.side.value)
        if i in hit_targets: icon = "✅"
        elif i == next_tp_index: icon = "🚀"
        else: icon = "⏳"
        line = f"  • {icon} TP{i}: <code>{_format_price(target.price.value)}</code> ({_format_pnl(pct_value)})"
        if 0 < target.close_percent < 100:
            line += f" | Close {target.close_percent:.0f}%"
        lines.append(line)
    return "\n".join(lines)

def _build_logbook_section(rec: Recommendation) -> str:
    lines = []
    log_events = [
        event for event in (rec.events or []) 
        if getattr(event, "event_type", "") in ("PARTIAL_CLOSE_MANUAL", "PARTIAL_CLOSE_AUTO", "FINAL_CLOSE")
    ]
    if not log_events:
        return ""
    lines.append("\n📋 <b>LOGBOOK</b>")
    for event in sorted(log_events, key=lambda ev: getattr(ev, "event_timestamp", datetime.min)):
        data = getattr(event, "event_data", {}) or {}
        pnl = data.get('pnl_on_part', 0.0)
        trigger = data.get('triggered_by', 'MANUAL')
        icon = "💰" if pnl >= 0 else "⚠️"
        lines.append(f"  • {icon} Closed {data.get('closed_percent', 0):.0f}% at <code>{_format_price(data.get('price', 0))}</code> ({_format_pnl(pnl)}) [{trigger}]")
    return "\n".join(lines)

def _build_summary_section(rec: Recommendation) -> str:
    pnl = _calculate_weighted_pnl(rec)
    return "\n".join([
        "📊 <b>TRADE SUMMARY</b>",
        f"💰 Entry: <code>{_format_price(rec.entry.value)}</code>",
        f"🏁 Final Exit Price: <code>{_format_price(rec.exit_price)}</code>",
        f"{'📈' if pnl >= 0 else '📉'} <b>Final Weighted Result: {_format_pnl(pnl)}</b> ({_get_result_text(pnl)})",
    ])

def build_trade_card_text(rec: Recommendation) -> str:
    """Builds the full text for a recommendation card."""
    # Use _get_attr for all domain object access
    header = _build_header(rec)
    parts = [header]
    
    if rec.status == RecommendationStatus.CLOSED:
        parts.append("─ ─ ─ ─ ─ ─ ─ ─ ─ ─")
        parts.append(_build_summary_section(rec))
        parts.append(_build_logbook_section(rec))
    else:
        if section := _build_live_price_section(rec): parts.append(section)
        parts.append(_build_performance_section(rec))
        parts.append(_build_exit_plan_section(rec))
        if section := _build_logbook_section(rec): parts.append(section)

    parts.append("────────────────────")
    parts.append(f"#{_get_attr(rec.asset, 'value')} #Signal")
    if rec.notes: parts.append(f"📝 Notes: <i>{rec.notes}</i>")
    return "\n".join(filter(None, parts))

# ---------------------------
# Internal Keyboard Builders (Moved from keyboards.py)
# ---------------------------
def public_channel_keyboard(rec_id: int, bot_username: Optional[str]) -> Optional[InlineKeyboardMarkup]:
     """Builds the simple 'Track Signal' button for public channels."""
     buttons = []
     if bot_username:
         track_url = f"https://t.me/{bot_username}?start=track_{rec_id}"
         buttons.append(InlineKeyboardButton("📊 Track Signal", url=track_url))
     return InlineKeyboardMarkup([buttons]) if buttons else None

# ---------------------------
# TelegramNotifier Class
# ---------------------------
class TelegramNotifier:
    """Handles all outbound communication to the Telegram Bot API."""

    def __init__(self) -> None:
        self.bot_token: Optional[str] = settings.TELEGRAM_BOT_TOKEN
        self.api_base: Optional[str] = (f"https://api.telegram.org/bot{self.bot_token}" if self.bot_token else None)
        self.ptb_app: Optional[Application] = None
        self._bot_username: Optional[str] = None

    def set_ptb_app(self, ptb_app: Application):
        """Injects the running PTB application instance into the notifier."""
        self.ptb_app = ptb_app
    
    @property
    def bot_username(self) -> Optional[str]:
        """Lazily fetches and caches the bot's username."""
        if self._bot_username:
            return self._bot_username
        if self.ptb_app and self.ptb_app.bot:
            self._bot_username = self.ptb_app.bot.username
            return self.ptb_app.bot.username
        if self.ptb_app and hasattr(self.ptb_app, 'bot'):
             try:
                  self._bot_username = self.ptb_app.bot.username
                  return self._bot_username
             except Exception:
                  pass 
        return None

    async def _post_async(self, method: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Performs an asynchronous POST request to the Telegram API."""
        if not self.api_base:
            log.warning("TelegramNotifier is not configured (no BOT token). Skipping '%s'.", method)
            return None
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{self.api_base}/{method}", json=payload)
                resp.raise_for_status()
                data = resp.json()
            if not data.get("ok"):
                log.error("Telegram API error on %s: %s (payload=%s)", method, data.get("description", "unknown"), payload)
                return None
            return data.get("result")
        except httpx.HTTPStatusError as e:
            body = e.response.text if getattr(e, "response", None) is not None else "<no-body>"
            log.error("Telegram API HTTP error on %s: %s | body=%s", method, e, body)
            return None
        except Exception:
            log.exception("Telegram API call '%s' failed with exception", method)
            return None
            
    async def _send_text(self, chat_id: Union[int, str], text: str, keyboard: Optional[InlineKeyboardMarkup] = None, **kwargs) -> Optional[Tuple[int, int]]:
        """Sends a text message using the async post method."""
        payload: Dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True, **kwargs}
        if keyboard:
            payload["reply_markup"] = keyboard.to_dict()
        
        result = await self._post_async("sendMessage", payload)
        if result and "message_id" in result and "chat" in result:
            try:
                return (int(result["chat"]["id"]), int(result["message_id"]))
            except (ValueError, TypeError):
                pass
        return None

    async def _edit_text(self, chat_id: Union[int, str], message_id: int, text: str, keyboard: Optional[InlineKeyboardMarkup] = None) -> bool:
        """Edits a text message using the async post method."""
        payload: Dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if keyboard:
            payload["reply_markup"] = keyboard.to_dict()
        return bool(await self._post_async("editMessageText", payload))

    # --- Public Methods ---
    
    async def post_to_channel(self, channel_id: Union[int, str], rec: Recommendation, keyboard: Optional[InlineKeyboardMarkup] = None) -> Optional[Tuple[int, int]]:
        """Builds and sends a recommendation card to a channel."""
        text = build_trade_card_text(rec)
        if keyboard is None:
            keyboard = public_channel_keyboard(rec.id, self.bot_username)
        return await self._send_text(chat_id=channel_id, text=text, keyboard=keyboard)

    async def post_notification_reply(self, chat_id: Union[int, str], message_id: int, text: str) -> Optional[Tuple[int, int]]:
        """Sends a reply to an existing message."""
        return await self._send_text(chat_id=chat_id, text=text, reply_to_message_id=message_id, allow_sending_without_reply=True)

    async def send_private_text(self, chat_id: Union[int, str], text: str):
        """Sends a simple private text message."""
        await self._send_text(chat_id=chat_id, text=text)

    async def edit_recommendation_card_by_ids(self, channel_id: Union[int, str], message_id: int, rec: Recommendation) -> bool:
        """Builds and edits an existing recommendation card."""
        new_text = build_trade_card_text(rec)
        keyboard = public_channel_keyboard(rec.id, self.bot_username) if rec.status != RecommendationStatus.CLOSED else None
        return await self._edit_text(
            chat_id=channel_id,
            message_id=message_id,
            text=new_text,
            keyboard=keyboard,
        )
# --- END OF FILE ---