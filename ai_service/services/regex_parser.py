# ai_service/services/regex_parser.py
"""
محلل Regex (المسار السريع).
يقرأ القوالب (Templates) من جدول `parsing_templates` المشترك
ويحاول مطابقتها.
"""

import re
import unicodedata
import logging
from typing import Dict, Any, Optional, List
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy import select

# استيراد النماذج وقاعدة البيانات المحلية لهذه الخدمة
from models import ParsingTemplate
from database import session_scope

log = logging.getLogger(__name__)

# --- دوال مساعدة منسوخة من النظام الرئيسي [compare: 164-181] ---

_AR_TO_EN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_SUFFIXES = {"K": Decimal("1000"), "M": Decimal("1000000"), "B": Decimal("1000000000")}

def _normalize_text(text: str) -> str:
    if not text: return ""
    s = unicodedata.normalize("NFKC", text)
    s = s.translate(_AR_TO_EN_DIGITS)
    s = s.replace("،", ",")
    s = re.sub(r'[^\w\s\u0600-\u06FF@:.,\d\-+%$#/|]', ' ', s, flags=re.UNICODE)
    s = re.sub(r'(\r\n|\r|\n){2,}', '\n', s)
    s = re.sub(r'\s{2,}', ' ', s)
    return s.strip()

def _normalize_for_key(text: str) -> str:
    return _normalize_text(text).upper()

def _parse_one_number(token: str) -> Optional[str]:
    """
    يحلل الرقم ويعيده كنص (string) لسلامة JSON.
    يعيد None إذا كان غير صالح.
    """
    if token is None: return None
    try:
        t = str(token).strip().replace(",", "").upper()
        if not t: return None
        multiplier = Decimal("1")
        num_part = t
        if t[-1].isalpha() and t[-1] in _SUFFIXES:
            multiplier = _SUFFIXES[t[-1]]
            num_part = t[:-1]
        if not re.fullmatch(r"[+\-]?\d*\.?\d+", num_part):
            return None
        
        val = Decimal(num_part) * multiplier
        return str(val) if val.is_finite() and val > 0 else None
    except Exception:
        return None

def _parse_targets_list(tokens: List[str]) -> List[Dict[str, Any]]:
    parsed_targets = []
    if not tokens: return parsed_targets
    for token in tokens:
        if not token: continue
        try:
            price_str, pct_str = token, ""
            if '@' in token:
                parts = token.split('@', 1)
                if len(parts) != 2: price_str = parts[0].strip(); pct_str = ""
                else: price_str, pct_str = parts[0].strip(), parts[1].strip().replace('%','')
            
            price_val_str = _parse_one_number(price_str)
            pct_val_str = _parse_one_number(pct_str) if pct_str else "0"
            
            pct_f = 0.0
            if pct_val_str:
                try: pct_f = float(pct_val_str)
                except ValueError: pct_f = 0.0
            
            if price_val_str is not None:
                parsed_targets.append({"price": price_val_str, "close_percent": pct_f})
        except Exception:
            continue
    
    if parsed_targets and all(t["close_percent"] == 0.0 for t in parsed_targets):
        parsed_targets[-1]["close_percent"] = 100.0
    return parsed_targets

def _find_side(text: str) -> Optional[str]:
    txt = text.upper()
    side_maps = {
        'LONG': ('long', 'buy', 'شراء', 'صعود'),
        'SHORT': ('short', 'sell', 'بيع', 'هبوط'),
    }
    for s, keywords in side_maps.items():
        if any(re.search(r'\b' + re.escape(kw.upper()) + r'\b', txt) for kw in keywords):
            return s
    return None

# --- الوظيفة الرئيسية للمحلل ---

def parse_with_regex(text: str, session: Session) -> Optional[Dict[str, Any]]:
    """
    يحاول تحليل النص باستخدام جميع قوالب Regex النشطة من قاعدة البيانات المشتركة.
    """
    try:
        # قراءة القوالب (Templates) من قاعدة البيانات
        stmt = select(ParsingTemplate).where(ParsingTemplate.is_public == True) # مثال: استخدام القوالب العامة فقط
        templates = session.execute(stmt).scalars().all()
    except Exception as e:
        log.error(f"RegexParser: Failed to query templates from DB: {e}")
        return None # لا يمكن المتابعة بدون قوالب

    if not templates:
        log.debug("RegexParser: No public templates found in DB.")
        return None

    normalized_upper = _normalize_for_key(text)
    
    for template in templates:
        try:
            pattern = template.pattern_value
            if not pattern: continue
            
            match = re.search(pattern, normalized_upper, re.IGNORECASE | re.MULTILINE | re.DOTALL)
            if not match: continue

            data = match.groupdict()
            parsed = {}

            # استخراج الحقول الأساسية
            parsed['asset'] = (data.get('asset') or '').strip().upper()
            side_cand = (data.get('side') or '').strip().upper()
            
            # استخدام _find_side للتحقق
            parsed['side'] = _find_side(normalized_upper)
            if not parsed['side'] and side_cand:
                 parsed['side'] = 'LONG' if 'LONG' in side_cand else ('SHORT' if 'SHORT' in side_cand else None)

            if not parsed['asset'] or not parsed['side']:
                continue # القالب طابق ولكن الحقول الأساسية مفقودة

            # تحليل الأرقام كنصوص
            parsed['entry'] = _parse_one_number(data.get('entry',''))
            parsed['stop_loss'] = _parse_one_number(data.get('sl', data.get('stop_loss','')))
            
            target_str = (data.get('targets') or data.get('targets_str') or '').strip()
            tokens = [t for t in re.split(r'[\s,\n,]+', target_str) if t]
            parsed['targets'] = _parse_targets_list(tokens)

            # التحقق من وجود جميع الحقول المطلوبة (كنصوص)
            required_keys = ['asset', 'side', 'entry', 'stop_loss', 'targets']
            if not all(parsed.get(k) for k in required_keys):
                continue # لم يتم العثور على جميع الحقول

            log.info(f"RegexParser: Matched template ID {template.id} for text snippet: {text[:50]}...")
            
            # إضافة حقول افتراضية إذا لم تكن موجودة
            parsed.setdefault("market", "Futures")
            parsed.setdefault("order_type", "LIMIT")
            parsed.setdefault("notes", data.get('notes'))

            return parsed # إرجاع أول مطابقة ناجحة

        except Exception as e:
            log.warning(f"RegexParser: Error applying template ID {template.id}: {e}")
            continue # انتقل إلى القالب التالي

    # لم يتم العثور على أي قالب مطابق
    log.debug("RegexParser: No templates matched.")
    return None