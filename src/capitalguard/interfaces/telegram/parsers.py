# src/capitalguard/interfaces/telegram/parsers.py (v1.4.0 - Production Ready)
"""
مُحللات النصوص المُحسَنة لبيئة الإنتاج
✅ تحليل دقيق وسريع لجميع التنسيقات
✅ دعم كامل للأرقام العربية والإنجليزية
✅ معالجة محسنة للأخطاء
"""

import re
import logging
import warnings
from typing import Dict, Any, List, Optional
from decimal import Decimal, InvalidOperation

__version__ = "1.4.0"
__compatible_with__ = "conversation_handlers >= v35.0"

log = logging.getLogger(__name__)

# --- Localization & Normalization ---
_AR_TO_EN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_SUFFIXES = {"K": Decimal("1000"), "M": Decimal("1000000"), "B": Decimal("1000000000")}

def _normalize_text(s: str) -> str:
    """تطبيع الأرقام والرموز العربية إلى الإنجليزية"""
    if not s:
        return ""
    s = s.translate(_AR_TO_EN_DIGITS)
    s = s.replace("،", ",").replace("؛", ";").replace("؟", "?")
    s = re.sub(r'\s+', ' ', s.strip())  # إزالة المسافات الزائدة
    return s

# --- Core Parsers ---

def parse_number(token: str) -> Optional[Decimal]:
    """تحليل الرمز الرقمي إلى Decimal مع دعم اللاحقات K, M, B"""
    if not token:
        return None
        
    try:
        t = _normalize_text(token).upper().replace(",", "").replace(" ", "")
        multiplier = Decimal("1")
        num_part = t

        # التحقق من اللواحق
        if t.endswith(tuple(_SUFFIXES.keys())):
            multiplier = _SUFFIXES[t[-1]]
            num_part = t[:-1]

        # التحقق من التنسيق الرقمي
        if not re.fullmatch(r"[+\-]?\d*\.?\d+", num_part):
            return None

        result = Decimal(num_part) * multiplier
        return result if result > 0 else None
        
    except (InvalidOperation, TypeError, ValueError) as e:
        log.debug(f"Failed to parse number: {token}, error: {e}")
        return None

def parse_targets_list(tokens: List[str]) -> List[Dict[str, Any]]:
    """تحليل قائمة الأهداف مثل ['60k@50', '62k@50']"""
    parsed_targets = []
    
    for token in tokens:
        if not token or token.strip() == "":
            continue
            
        try:
            price_str, close_pct_str = token, "0"
            if '@' in token:
                parts = token.split('@', 1)
                if len(parts) != 2:
                    continue
                price_str, close_pct_str = parts[0].strip(), parts[1].strip()

            price = parse_number(price_str)
            close_pct = parse_number(close_pct_str) if close_pct_str else Decimal("0")

            if price is not None and close_pct is not None:
                parsed_targets.append({
                    "price": price, 
                    "close_percent": float(close_pct)
                })
                
        except Exception as e:
            log.warning(f"Failed to parse target token: {token}, error: {e}")
            continue

    # إذا لم تكن هناك أهداف مع نسب، ضع 100% للهدف الأخير
    if parsed_targets and all(t["close_percent"] == 0.0 for t in parsed_targets):
        parsed_targets[-1]["close_percent"] = 100.0

    return parsed_targets

def parse_rec_command(text: str) -> Optional[Dict[str, Any]]:
    """تحليل أمر التوصية السريع"""
    try:
        normalized_text = _normalize_text(text)
        parts = normalized_text.split()
        
        if not parts or len(parts) < 5:
            return None

        asset = parts[0].upper()
        side = parts[1].upper()
        
        # التحقق من الاتجاه الصحيح
        if side not in ["LONG", "SHORT"]:
            return None
            
        entry = parse_number(parts[2])
        stop_loss = parse_number(parts[3])
        target_tokens = parts[4:]

        targets = parse_targets_list(target_tokens)
        if not targets:
            return None

        return {
            "asset": asset,
            "side": side,
            "entry": entry,
            "stop_loss": stop_loss,
            "targets": targets,
            "market": "Futures",
            "order_type": "LIMIT",
        }
        
    except (ValueError, IndexError, TypeError) as e:
        log.error(f"Error parsing rec command: {e}, text: {text}")
        return None

def parse_editor_command(text: str) -> Optional[Dict[str, Any]]:
    """تحليل نص المحرر بتنسيق مفتاح:قيمة"""
    data: Dict[str, Any] = {}
    key_map = {
        "asset": ["asset", "symbol", "أصل", "رمز"],
        "side": ["side", "type", "اتجاه", "نوع"],
        "entry": ["entry", "سعر الدخول", "دخول"],
        "stop_loss": ["stop_loss", "stop", "sl", "وقف الخسارة", "وقف"],
        "targets": ["targets", "tps", "أهداف", "اهداف"],
        "market": ["market", "سوق"],
        "notes": ["notes", "note", "ملاحظات", "ملاحظة"],
    }
    reverse_key_map = {alias.lower(): key for key, aliases in key_map.items() for alias in aliases}

    lines = text.strip().split("\n")
    
    for raw_line in lines:
        line = _normalize_text(raw_line)
        if ":" not in line:
            continue

        try:
            key_str, value_str = line.split(":", 1)
            key_str = key_str.strip().lower()
            value_str = value_str.strip()

            if key_str in reverse_key_map:
                main_key = reverse_key_map[key_str]

                if main_key == "targets":
                    data[main_key] = parse_targets_list(value_str.split())
                elif main_key in ["entry", "stop_loss"]:
                    data[main_key] = parse_number(value_str)
                elif main_key in ["asset", "side", "market"]:
                    data[main_key] = value_str.upper()
                else:
                    data[main_key] = value_str
                    
        except (ValueError, IndexError) as e:
            log.warning(f"Could not parse line in editor mode: '{raw_line}'. Error: {e}")
            continue

    # التحقق من البيانات المطلوبة
    required_keys = ["asset", "side", "entry", "stop_loss", "targets"]
    if not all(k in data for k in required_keys):
        return None

    # القيم الافتراضية
    data.setdefault("market", "Futures")
    data.setdefault("order_type", "LIMIT")
    data.setdefault("notes", "")

    return data

# --- Backward Compatibility Layer ---

def parse_quick_command(text: str):
    """اسم مستعار للتوافق مع الإصدارات القديمة"""
    warnings.warn(
        "parse_quick_command() is deprecated. Use parse_rec_command() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return parse_rec_command(text)

def parse_text_editor(text: str):
    """اسم مستعار للتوافق مع الإصدارات القديمة"""
    warnings.warn(
        "parse_text_editor() is deprecated. Use parse_editor_command() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return parse_editor_command(text)

# --- Validation Helpers ---

def validate_recommendation_data(data: Dict[str, Any]) -> tuple[bool, str]:
    """التحقق من صحة بيانات التوصية"""
    try:
        if not data.get("asset"):
            return False, "الرمز مطلوب"
            
        if data.get("side") not in ["LONG", "SHORT"]:
            return False, "الاتجاه يجب أن يكون LONG أو SHORT"
            
        if not data.get("entry") or data["entry"] <= 0:
            return False, "سعر الدخول غير صالح"
            
        if not data.get("stop_loss") or data["stop_loss"] <= 0:
            return False, "وقف الخسارة غير صالح"
            
        if not data.get("targets") or len(data["targets"]) == 0:
            return False, "يجب تحديد هدف واحد على الأقل"
            
        # التحقق من أن وقف الخسارة في الجانب الصحيح
        entry = data["entry"]
        stop_loss = data["stop_loss"]
        side = data["side"]
        
        if side == "LONG" and stop_loss >= entry:
            return False, "لـ LONG، يجب أن يكون وقف الخسارة أقل من سعر الدخول"
            
        if side == "SHORT" and stop_loss <= entry:
            return False, "لـ SHORT، يجب أن يكون وقف الخسارة أعلى من سعر الدخول"
            
        return True, "صالح"
        
    except Exception as e:
        return False, f"خطأ في التحقق: {str(e)}"