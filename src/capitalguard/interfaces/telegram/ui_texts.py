# File: src/capitalguard/interfaces/telegram/ui_texts.py
# Version: v29.1.0-R2 (Design 3 - Trade View)
# ✅ THE FIX: (R2 Feature - Design 3)
#    - 1. (REFACTORED) إعادة كتابة `build_trade_card_text` بالكامل.
#    - 2. (NEW) تنفذ "التصميم 3" (تصميم TradingView/Binance)
#       مع كتل نظيفة لـ "الأداء"، "السعر الحي"، و "خطة الخروج".
#    - 3. (CLEAN) استخدام `_get_attr` و `_format_price` المستوردة
#       لضمان الاتساق والنظافة.
# 🎯 IMPACT: هذه هي واجهة عرض التفاصيل النهائية والاحترافية للنظام.

from __future__ import annotations
import logging
from typing import List, Optional, Dict, Any
from decimal import Decimal, InvalidOperation
from datetime import datetime

from capitalguard.domain.entities import Recommendation, RecommendationStatus
from capitalguard.domain.value_objects import Target
# ✅ R2: Import helpers from a single source
from capitalguard.interfaces.telegram.helpers import _get_attr, _to_decimal, _pct, _format_price

log = logging.getLogger(__name__)

_STATUS_MAP = {
    RecommendationStatus.PENDING: "⏳ PENDING",
    RecommendationStatus.ACTIVE: "⚡️ ACTIVE",
    RecommendationStatus.CLOSED: "🏁 CLOSED",
}
_SIDE_ICONS = {'LONG': '🟢', 'SHORT': '🔴'}

# --- (Helpers _to_decimal, _pct, _format_price, _get_attr
#      are now imported from helpers.py or keyboards.py) ---

def _format_pnl(pnl: float) -> str:
    return f"{pnl:+.2f}%"

def _rr(entry: Any, sl: Any, targets: List[Target]) -> str:
    """Calculates Risk/Reward based on the *first* target."""
    try:
        entry_dec, sl_dec = _to_decimal(entry), _to_decimal(sl)
        if not targets:
            return "1:—"
        
        first_target = targets[0]
        first_target_price = _to_decimal(_get_attr(first_target, 'price'))

        if not entry_dec.is_finite() or not sl_dec.is_finite() or not first_target_price.is_finite():
            return "1:—"
            
        risk = abs(entry_dec - sl_dec)
        if risk.is_zero(): return "1:∞"
        
        reward = abs(first_target_price - entry_dec)
        ratio = reward / risk
        return f"1:{ratio:.2f}"
    except Exception:
        return "1:—"

def _calculate_weighted_pnl(rec: Recommendation) -> float:
    """Calculates final PnL based on event log for partial closures."""
    total_pnl_contribution = 0.0
    total_percent_closed = 0.0
    
    closure_event_types = ("PARTIAL_CLOSE_MANUAL", "PARTIAL_CLOSE_AUTO", "FINAL_CLOSE")

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

    # If not fully closed, return the contribution so far
    return total_pnl_contribution

def _get_result_text(pnl: float) -> str:
    if pnl > 0.001: return "🏆 WIN"
    elif pnl < -0.001: return "💔 LOSS"
    else: return "🛡️ BREAKEVEN"

def _build_header(rec: Recommendation) -> str:
    """Builds the header (Design 3)."""
    status_text = _STATUS_MAP.get(_get_attr(rec, 'status'), "UNKNOWN")
    side_icon = _SIDE_ICONS.get(_get_attr(rec, 'side'), '⚪')
    id_prefix = "Trade" if getattr(rec, 'is_user_trade', False) else "Signal"
    
    # ✅ R2 (Design 3): New Header
    return f"{side_icon} *{_get_attr(rec.asset, 'value')} | {_get_attr(rec.side, 'value')} | #{rec.id}*"
    

def _build_live_price_section(rec: Recommendation) -> str:
    """Builds the Live Price block (Design 3)."""
    live_price = getattr(rec, "live_price", None)
    if _get_attr(rec, 'status') != RecommendationStatus.ACTIVE or live_price is None:
        return ""
        
    pnl = _pct(_get_attr(rec, 'entry'), live_price, _get_attr(rec, 'side'))
    pnl_icon = '🟢' if pnl >= 0 else '🔴'
    
    # Calculate price change (optional, stubbed)
    price_change_pct = "+0.00%" # (This would require fetching 24h data)

    return "\n".join([
        "━━━━━━━━━━━━━━━━━━",
        "📈 *السعر الحيّ*",
        f"`{_format_price(live_price)}`  `({price_change_pct})`",
        f"PnL: `{_format_pnl(pnl)}` {pnl_icon}"
    ])

def _build_performance_section(rec: Recommendation) -> str:
    """Builds the Performance block (Design 3)."""
    entry_price = _get_attr(rec, 'entry')
    stop_loss = _get_attr(rec, 'stop_loss')
    targets = _get_attr(rec, 'targets', [])
    targets_list = targets.values if hasattr(targets, 'values') else []
    
    rr_str = _rr(entry_price, stop_loss, targets_list)
    
    # PnL logic for non-active states
    pnl_str = "—"
    live_price = getattr(rec, "live_price", None)
    
    if _get_attr(rec, 'status') == RecommendationStatus.ACTIVE and live_price is not None:
        pnl = _pct(entry_price, live_price, _get_attr(rec, 'side'))
        pnl_str = f"{_format_pnl(pnl)}"
    elif _get_attr(rec, 'status') == RecommendationStatus.CLOSED:
        pnl = _calculate_weighted_pnl(rec)
        pnl_str = f"{_format_pnl(pnl)} (Final)"

    return "\n".join([
        "📊 *الأداء*",
        f"Entry: `{_format_price(entry_price)}`",
        f"Stop: `{_format_price(stop_loss)}`",
        f"Risk/Reward: `{rr_str}`",
        f"PnL: `{pnl_str}`"
    ])

def _build_exit_plan_section(rec: Recommendation) -> str:
    """Builds the Exit Plan block (Design 3)."""
    lines = ["🎯 *خطة الخروج*"]
    entry_price = _get_attr(rec, 'entry')
    targets = _get_attr(rec, 'targets', [])
    targets_list = targets.values if hasattr(targets, 'values') else []
    
    hit_targets = set()
    if rec.events:
        for event in rec.events:
            if event.event_type.startswith("TP") and event.event_type.endswith("_HIT"):
                try:
                    target_num = int(event.event_type[2:-4])
                    hit_targets.add(target_num)
                except (ValueError, IndexError):
                    continue
                    
    if not targets_list:
        return "🎯 *خطة الخروج*\n • `N/A`"

    for i, target in enumerate(targets_list, start=1):
        pct_value = _pct(entry_price, _get_attr(target, 'price'), _get_attr(rec, 'side'))
        icon = "✅" if i in hit_targets else "⏳"
        
        line = f"  • {icon} TP{i}: `{_format_price(_get_attr(target, 'price'))}` ({_format_pnl(pct_value)})"
        
        close_pct = _get_attr(target, 'close_percent', 0.0)
        if 0 < close_pct < 100:
            line += f" | Close {close_pct:.0f}%"
        elif close_pct == 100 and i == len(targets_list):
            line += " | Close 100%"
            
        lines.append(line)
    return "\n".join(lines)

def _build_summary_section(rec: Recommendation) -> str:
    """Builds the Summary block for CLOSED trades (Design 3)."""
    pnl = _calculate_weighted_pnl(rec)
    return "\n".join([
        "📊 *ملخص الصفقة*",
        f"Entry: `{_format_price(_get_attr(rec, 'entry'))}`",
        f"🏁 Final Exit: `{_format_price(_get_attr(rec, 'exit_price'))}`",
        f"{'📈' if pnl >= 0 else '📉'} *Final Result: {_format_pnl(pnl)}* ({_get_result_text(pnl)})",
    ])

def build_trade_card_text(rec: Recommendation) -> str:
    """
    ✅ R2 (Design 3): Builds the full trade detail view.
    """
    header = _build_header(rec)
    parts = [header]
    
    if _get_attr(rec, 'status') == RecommendationStatus.CLOSED:
        parts.append("━━━━━━━━━━━━━━━━━━")
        parts.append(_build_summary_section(rec))
        parts.append("━━━━━━━━━━━━━━━━━━")
    else:
        # Build Active/Pending view
        if section := _build_live_price_section(rec):
            parts.append(section)
        
        parts.append("━━━━━━━━━━━━━━━━━━")
        parts.append(_build_performance_section(rec))
        parts.append("━━━━━━━━━━━━━━━━━━")
        parts.append(_build_exit_plan_section(rec))
        parts.append("━━━━━━━━━━━━━━━━━━")
    
    if rec.notes:
        parts.append(f"📝 Notes: *{rec.notes}*")
        
    # Escape for MarkdownV2
    # (Note: The handler (management_handlers.py) is responsible for escaping)
    return "\n".join(filter(None, parts))

def build_review_text_with_price(draft: dict, preview_price: Optional[float]) -> str:
    """Builds the text for the creation review card."""
    asset, side, market = draft.get("asset", "N/A"), draft.get("side", "N/A"), draft.get("market", "Futures")
    entry, sl = draft.get("entry", Decimal(0)), draft.get("stop_loss", Decimal(0))
    raw_tps = draft.get("targets", [])
    target_lines = []
    for i, t in enumerate(raw_tps, start=1):
        price = _to_decimal(t.get('price', 0))
        pct_value = _pct(entry, price, side)
        close_percent = t.get('close_percent', 0)
        suffix = f" (Close {close_percent:.0f}%)" if 0 < close_percent < 100 else ""
        if close_percent == 100 and i == len(raw_tps): suffix = ""
        
        target_lines.append(f"  • TP{i}: <code>{_format_price(price)}</code> ({_format_pnl(pct_value)}){suffix}")
    
    base_text = (
        f"📝 <b>REVIEW RECOMMENDATION</b>\n"
        f"─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"
        f"<b>{asset} | {market} / {side}</b>\n\n"
        f"💰 Entry: <code>{_format_price(entry)}</code>\n"
        f"🛑 Stop: <code>{_format_price(sl)}</code>\n"
        f"🎯 Targets:\n" + "\n".join(target_lines) + "\n"
    )
    if preview_price is not None:
        base_text += f"\n💹 Current Price: <code>{_format_price(preview_price)}</code>"
    
    base_text += "\n\nReady to publish?"
    return base_text