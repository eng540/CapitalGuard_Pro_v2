# ai_service/services/parsing_utils.py
"""
(v1.3.0) - Advanced Percentage Extraction.
✅ HOTFIX: (Based on user technical report)
✅ Implemented vastly improved Regex patterns in `_extract_each_percentage_from_text`
to understand complex Arabic and English global percentage formats
(e.g., "كل هدف 25%", "20% per target", "Close 30% each TP").
"""

import re
import logging
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, List, Optional

log = logging.getLogger(__name__)

# --- الثوابت وأدوات التطبيع ---
_AR_TO_EN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_SUFFIXES = {"K": Decimal("1000"), "M": Decimal("1000000"), "B": Decimal("1000000000")}

def _normalize_arabic_numerals(s: str) -> str:
    """يحول الأرقام العربية فقط."""
    if not s:
        return ""
    return s.translate(_AR_TO_EN_DIGITS)

def parse_decimal_token(token: str) -> Optional[Decimal]:
    """
    (مصدر الحقيقة)
    يحلل رمزًا رقميًا واحدًا (يدعم K/M/B) إلى Decimal.
    """
    if token is None:
        return None
    try:
        s = _normalize_arabic_numerals(str(token)).strip().lower().replace(',', '')
        if not s:
            return None

        multiplier = Decimal("1")
        num_part = s
        
        if s.endswith('k'):
            multiplier = _SUFFIXES["K"]
            num_part = s[:-1]
        elif s.endswith('m'):
            multiplier = _SUFFIXES["M"]
            num_part = s[:-1]
        elif s.endswith('b'):
            multiplier = _SUFFIXES["B"]
            num_part = s[:-1]

        if not num_part: 
             return None
             
        if not re.fullmatch(r"[+\-]?\d*\.?\d+", num_part):
            return None

        val = Decimal(num_part) * multiplier
        return val if val.is_finite() and val >= 0 else None

    except (InvalidOperation, TypeError, ValueError) as e:
        log.debug(f"Failed to parse Decimal token: '{token}', error: {e}")
        return None

def _parse_token_price_and_pct(token: str) -> Dict[str, Optional[Decimal]]:
    """
    يحلل رمز هدف واحد (مثل "6k@25%") إلى Decimal.
    """
    if not token or not str(token).strip():
        raise ValueError("Empty target token")
    token = str(token).strip()
    
    price_part, pct_part = token, "0"
    
    if '@' in token:
        parts = token.split('@', 1)
        if len(parts) == 2:
            price_part, pct_part = parts[0], parts[1].strip().rstrip('%')
        else:
            price_part = parts[0]
    
    price = parse_decimal_token(price_part)
    pct = parse_decimal_token(pct_part)
    
    return {"price": price, "pct": pct}

def _extract_each_percentage_from_text(source_text: str) -> Optional[Decimal]:
    """
    (v1.3.0) يبحث عن أنماط النسبة المئوية العامة (بناءً على تقرير المراجعة).
    """
    if not source_text:
        return None
    
    normalized_text = _normalize_arabic_numerals(source_text)
    
    # ✅ (Point 1) الأنماط الجديدة المقترحة
    patterns = [
        # (20% each), 20% per target, كل هدف 20%, 20% كل منها
        r'\(?\s*(\d{1,3}(?:\.\d+)?)\s*%\s*(?:each|per target|لكل هدف|كل هدف|كل منها)\)?',
        
        # (Close 30% each TP), (اغلاق 25% عند كل هدف)
        # (ملاحظة: هذا قد يلتقط النسبة حتى لو لم تكن "each")
        r'(?:(?:close|اغلاق|إغلاق)\s*)(\d{1,3}(?:\.\d+)?)\s*%?(?:\s*(?:each|TP|هدف|targets|عند كل هدف))?',
        
        # (النسبة 25%) , (بنسبة 25%)
        r'(?:(?:لكل|بنسبة|النسبة)\s*)(\d{1,3}(?:\.\d+)?)\s*%?',
        
        # (20 each target) - بدون رمز %
        r'(\d{1,3}(?:\.\d+)?)\s*(?:each|لكل)\s*(?:هدف|TP|target)'
    ]
    
    for pattern in patterns:
        m = re.search(pattern, normalized_text, re.IGNORECASE)
        if m:
            try:
                val = Decimal(m.group(1))
                if 0 <= val <= 100:
                    log.debug(f"Found global percentage: {val}% using pattern: {pattern}")
                    return val
            except Exception:
                continue # جرب النمط التالي
    return None

def normalize_targets(
    targets_raw: Any, 
    source_text: str = ""
) -> List[Dict[str, Any]]:
    """
    (مصدر الحقيقة - v1.2)
    يطبع قائمة الأهداف.
    """
    normalized: List[Dict[str, Any]] = []
    if not targets_raw:
        return normalized

    # ✅ (Point 1) سيتم الآن العثور على النسب الصحيحة هنا
    each_pct = _extract_each_percentage_from_text(source_text)

    # الحالة 1: قائمة من الكائنات (التنسيق الصحيح)
    if isinstance(targets_raw, list) and targets_raw and isinstance(targets_raw[0], dict):
        for t in targets_raw:
            try:
                raw_price = t.get("price") if isinstance(t, dict) else t
                price_val = parse_decimal_token(str(raw_price))
                if price_val is None or price_val <= 0:
                    continue
                
                close_pct_raw = t.get("close_percent", None)
                close_pct = Decimal(str(close_pct_raw)) if close_pct_raw is not None else None
                
                if close_pct is None and each_pct is not None:
                    close_pct = each_pct
                elif close_pct is None:
                    close_pct = Decimal("0")

                normalized.append({"price": str(price_val), "close_percent": float(close_pct)})
            except Exception as e:
                log.debug(f"Skipping malformed target dict entry: {t} ({e})")

    # الحالة 2: قائمة من القيم الأولية (أرقام، نصوص)
    elif isinstance(targets_raw, list):
        tokens_flat: List[str] = []
        for item in targets_raw:
            if item is None: continue
            s = _normalize_arabic_numerals(str(item)).strip()
            parts = re.split(r'[\s\n,/\-→]+', s)
            tokens_flat.extend([p.strip() for p in parts if p.strip()])

        for tok in tokens_flat:
            try:
                parsed = _parse_token_price_and_pct(tok)
                price = parsed["price"]
                pct = parsed["pct"]
                
                if price is None or price <= 0:
                    continue
                if pct is None and each_pct is not None:
                    pct = each_pct
                elif pct is None:
                    pct = Decimal("0")

                normalized.append({"price": str(price), "close_percent": float(pct)})
            except Exception as e:
                log.debug(f"Skipped token while normalizing targets: '{tok}' ({e})")
    
    # الحالة 3: نص واحد يحتوي على عدة أرقام
    elif isinstance(targets_raw, str):
        s = _normalize_arabic_numerals(targets_raw).strip()
        tokens = re.split(r'[\s\n,/\-→]+', s)
        
        for tok in tokens:
            tok = tok.strip()
            if not tok:
                continue
            try:
                parsed = _parse_token_price_and_pct(tok)
                price = parsed["price"]
                pct = parsed["pct"]

                if price is None or price <= 0:
                    continue
                if pct is None and each_pct is not None:
                    pct = each_pct
                elif pct is None:
                    pct = Decimal("0")
                    
                normalized.append({"price": str(price), "close_percent": float(pct)})
            except Exception:
                continue

    # تطبيق قاعدة الهدف الأخير (100%)
    # ✅ (Point 3) هذا المنطق سليم الآن، لأنه لن يتم تشغيله
    # إلا إذا فشل each_pct (المُحسَّن) *و* فشلت كل النسب الفردية.
    if normalized and all(t["close_percent"] == 0.0 for t in normalized):
        normalized[-1]["close_percent"] = 100.0
        
    return normalized