#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/services/regex_parser.py ---
# File: ai_service/services/regex_parser.py
# Version: 1.5.0 (Decoupled)
# ✅ THE FIX: (Protocol 1) تم فصل الخدمة عن قاعدة البيانات.
#    - `parse_with_regex` لم تعد تتلقى `session`. بدلاً من ذلك، تتلقى `user_id` (اختياري).
#    - تمت إزالة منطق جلب القوالب من قاعدة البيانات (DB templates).
#    - تعتمد الخدمة الآن *فقط* على محلل Key-Value البسيط كـ "مسار سريع".
# 🎯 IMPACT: هذا الملف لم يعد يتصل بقاعدة البيانات. (لتحسين هذا، يجب نقل منطق قوالب DB إلى `api`).

import re
import unicodedata
import logging
from typing import Dict, Any, Optional, List
from decimal import Decimal

# ❌ REMOVED DB IMPORTS
# from sqlalchemy.orm import Session
# from sqlalchemy import select
# from models import ParsingTemplate
# from database import session_scope

# --- ✅ استيراد مصدر الحقيقة الوحيد ---
from services.parsing_utils import (
    parse_decimal_token, 
    normalize_targets
)

log = logging.getLogger(__name__)

# --- دوال مساعدة (للتطبيع والبحث فقط) ---

_AR_TO_EN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

def _normalize_text(text: str) -> str:
    if not text: return ""
    s = unicodedata.normalize("NFKC", text)
    s = s.translate(_AR_TO_EN_DIGITS)
    s = s.replace("،", ",")
    s = re.sub(r'[^\w\s\u0600-\u06FF@:.,\d\-+%$#/|→]', ' ', s, flags=re.UNICODE)
    s = re.sub(r'(\r\n|\r|\n){2,}', '\n', s)
    s = re.sub(r'\s{2,}', ' ', s)
    return s.strip()

def _normalize_for_key(text: str) -> str:
    return _normalize_text(text).upper()

def _find_side(text: str) -> Optional[str]:
    txt = text.upper()
    side_maps = {
        'LONG': ('LONG', 'BUY', 'شراء', 'صعود'),
        'SHORT': ('SHORT', 'SELL', 'بيع', 'هبوط'),
    }
    for s, keywords in side_maps.items():
        if any(re.search(r'\b' + re.escape(kw) + r'\b', txt) for kw in keywords):
            return s
    return None

def _parse_simple_key_value(text: str) -> Optional[Dict[str, Any]]:
    """
    A simple fallback parser that looks for common Arabic/English keys.
    """
    try:
        normalized_upper = _normalize_for_key(text)
        
        # ✅ HOTFIX (v1.4): تم توسيع Regex الخاص بالأهداف ليشمل / - →
        keys = {
            'asset': r'#([A-Z0-9]{3,12})',
            'side': r'(LONG|BUY|شراء|صعود|SHORT|SELL|بيع|هبوط)',
            'entry': r'(ENTRY|دخول|مناطق الدخول|BUY)[:\s→]+([\d.,KMB]+)',
            'stop_loss': r'(SL|STOP|ايقاف خسارة|STOP LOSS)[:\s→]+([\d.,KMB]+)',
            'targets': r'(TARGETS|TP|الاهداف|اهداف|SELL TARGETS)[:\s\n→]+((?:[\d.,KMB@%\s\n/\-→]+))'
        }

        parsed = {}

        side_match = re.search(keys['side'], normalized_upper)
        if side_match:
            parsed['side'] = _find_side(side_match.group(1))

        asset_match = re.search(keys['asset'], normalized_upper)
        if asset_match:
            asset_str = asset_match.group(1)
            if asset_str in ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOT", "LINK", "MATIC", "AVAX", "TURTLE"]:
                parsed['asset'] = f"{asset_str}USDT"
            else:
                parsed['asset'] = asset_str
        
        entry_match = re.search(keys['entry'], normalized_upper)
        if entry_match:
            entry_val = parse_decimal_token(entry_match.group(2))
            parsed['entry'] = str(entry_val) if entry_val is not None else None

        sl_match = re.search(keys['stop_loss'], normalized_upper)
        if sl_match:
            sl_val = parse_decimal_token(sl_match.group(2))
            parsed['stop_loss'] = str(sl_val) if sl_val is not None else None

        targets_match = re.search(keys['targets'], normalized_upper, re.DOTALL)
        if targets_match:
            target_tokens_str = targets_match.group(2)
            parsed['targets'] = normalize_targets(target_tokens_str, source_text=text)
        
        required_keys = ['asset', 'side', 'entry', 'stop_loss', 'targets']
        if not all(parsed.get(k) for k in required_keys):
            log.debug(f"Simple KV parser found data, but missing required keys. Found: {parsed.keys()}")
            return None
        
        log.info(f"Simple KV parser successfully extracted data (Asset: {parsed['asset']})")
        parsed.setdefault("market", "Futures")
        parsed.setdefault("order_type", "LIMIT")
        parsed.setdefault("notes", None) 
        
        # ✅ REFACTORED: إرجاع البيانات مع Decimals
        # تحويل النصوص مرة أخرى إلى Decimal لتتسق مع مخرجات llm_parser
        parsed['entry'] = parse_decimal_token(parsed['entry'])
        parsed['stop_loss'] = parse_decimal_token(parsed['stop_loss'])
        # targets is already list[dict] with Decimals from normalize_targets
        
        return parsed

    except Exception as e:
        log.warning(f"Error in _parse_simple_key_value: {e}", exc_info=False)
        return None


# --- الوظيفة الرئيسية للمحلل ---
# ✅ REFACTORED: (Protocol 1)
def parse_with_regex(text: str, user_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """
    يحاول تحليل النص باستخدام قوالب Regex، ثم Key-Value البسيط.
    (تمت إزالة منطق قوالب DB - يعتمد الآن فقط على Simple KV)
    """
    
    # ❌ REMOVED: Database query for templates
    
    # --- 1. المسار السريع (قوالب DB) ---
    # (تمت إزالة هذا القسم. لإعادة تفعيله، يجب نقل هذا المنطق إلى `api`
    # وتمرير القوالب الخاصة بالمستخدم إلى `ai-service`)

    # --- 2. المسار الاحتياطي (محلل Key-Value البسيط) ---
    log.debug(f"User {user_id}: Trying simple Key-Value parser...")
    simple_result = _parse_simple_key_value(text)
    if simple_result:
        return simple_result

    log.debug("RegexParser: All regex paths failed.")
    return None
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/services/regex_parser.py ---