# --- START OF FINAL, CORRECTED FILE (V11): src/capitalguard/interfaces/telegram/parsers.py ---
import re
import unicodedata
from typing import Dict, Any, List, Optional
import logging

log = logging.getLogger(__name__)

# ✅ --- START: ADDED MISSING PARSING FUNCTIONS ---
# These functions were previously in management_handlers.py and are now centralized here.
_AR_TO_EN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_SUFFIXES = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
_SEPARATORS_REGEX = re.compile(r"[,\u060C;:|\t\r\n]+")

def _normalize_text(s: str) -> str:
    if not s: return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_AR_TO_EN_DIGITS)
    s = s.replace("،", ",")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _parse_one_number(token: str) -> float:
    if not token: raise ValueError("قيمة رقمية فارغة")
    t = token.strip().upper()
    t = re.sub(r"^[^\d+-.]+|[^\dA-Z.+-]+$", "", t)
    t = t.replace(",", "")
    m = re.match(r"^([+\-]?\d+(?:\.\d+)?)([KMB])?$", t)
    if not m: raise ValueError(f"قيمة رقمية غير صالحة: '{token}'")
    num_str, suf = m.groups()
    scale = _SUFFIXES.get(suf or "", 1)
    return float(num_str) * scale

def _tokenize_numbers(s: str) -> List[str]:
    s = _normalize_text(s)
    s = _SEPARATORS_REGEX.sub(" ", s)
    return [p for p in s.split(" ") if p]

def _coalesce_num_suffix_tokens(tokens: List[str]) -> List[str]:
    out: List[str] = []
    i = 0
    while i < len(tokens):
        cur = tokens[i].strip()
        nxt = tokens[i + 1].strip() if i + 1 < len(tokens) else None
        if nxt and re.fullmatch(r"[KMBkmb]", nxt):
            out.append(cur + nxt.upper())
            i += 2
        else:
            out.append(cur)
            i += 1
    return out

def parse_number(s: str) -> float:
    tokens = _tokenize_numbers(s)
    if not tokens: raise ValueError("لم يتم العثور على قيمة رقمية.")
    tokens = _coalesce_num_suffix_tokens(tokens)
    return _parse_one_number(tokens[0])

def parse_number_list(s: str) -> List[float]:
    tokens = _tokenize_numbers(s)
    if not tokens: raise ValueError("لم يتم العثور على أي أرقام.")
    tokens = _coalesce_num_suffix_tokens(tokens)
    return [_parse_one_number(t) for t in tokens]
# ✅ --- END: ADDED MISSING PARSING FUNCTIONS ---


def parse_quick_command(text: str) -> Optional[Dict[str, Any]]:
    try:
        pattern = re.compile(
            r'^\/rec\s+'
            r'([A-Z0-9\/]+)\s+'
            r'(LONG|SHORT)\s+'
            r'([\d.,]+)\s+'
            r'([\d.]+)\s+'
            r'([\d.\s,kK]+?)'
            r'(?:\s*--notes\s*\"(.*?)\")?'
            r'(?:\s*--market\s*(\w+))?'
            r'(?:\s*--risk\s*([\d.]+%?))?'
            r'\s*$', re.IGNORECASE
        )
        match = pattern.match(text)
        if not match: return None
        asset, side, entries_str, sl_str, targets_str, notes, market, risk = match.groups()
        entries = [float(e.strip()) for e in entries_str.replace(',', ' ').split()]
        targets = []
        for t in targets_str.replace(',', ' ').split():
            t = t.strip().lower()
            if not t: continue
            if 'k' in t: targets.append(float(t.replace('k', '')) * 1000)
            else: targets.append(float(t))
        return {
            "asset": asset.upper(), "side": side.upper(),
            "entry": entries[0] if len(entries) == 1 else entries,
            "stop_loss": float(sl_str), "targets": targets,
            "notes": notes if notes else None,
            "market": market.capitalize() if market else "Futures",
            "risk": risk if risk else None,
        }
    except (ValueError, IndexError) as e:
        log.error(f"Error parsing quick command: {e}")
        return None

def parse_text_editor(text: str) -> Optional[Dict[str, Any]]:
    data = {}
    lines = text.strip().split('\n')
    key_map = {
        'asset': ['asset', 'symbol', 'الأصل'], 'side': ['side', 'type', 'direction', 'الاتجاه'],
        'entry': ['entry', 'entries', 'دخول'], 'stop_loss': ['stop', 'sl', 'stoploss', 'إيقاف', 'وقف'],
        'targets': ['targets', 'tps', 'goals', 'أهداف'], 'notes': ['notes', 'note', 'ملاحظات'],
        'market': ['market', 'سوق'], 'risk': ['risk', 'مخاطرة']
    }
    for line in lines:
        try:
            key_str, value_str = line.split(':', 1)
            key_str, value_str = key_str.strip().lower(), value_str.strip()
            for key, aliases in key_map.items():
                if key_str in aliases:
                    if key in ['entry', 'targets']:
                        values = []
                        for v in value_str.replace(',', ' ').split():
                            v = v.strip().lower()
                            if not v: continue
                            if 'k' in v: values.append(float(v.replace('k', '')) * 1000)
                            else: values.append(float(v))
                        data[key] = values[0] if key == 'entry' and len(values) == 1 else values
                    elif key == 'stop_loss': data[key] = float(value_str)
                    else: data[key] = value_str
                    break
        except ValueError: continue
    if not all(k in data for k in ['asset', 'side', 'entry', 'stop_loss', 'targets']): return None
    return data
# --- END OF FINAL, CORRECTED FILE (V11) ---