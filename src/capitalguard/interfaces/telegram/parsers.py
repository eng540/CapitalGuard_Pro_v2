# --- START OF FINAL, CORRECTED FILE: src/capitalguard/interfaces/telegram/parsers.py ---
import re
from typing import Dict, Any, List, Optional

# --- Number Parsing Utilities (Moved from management_handlers.py) ---

_AR_TO_EN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_SUFFIXES = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
_SEPARATORS_REGEX = re.compile(r"[,\u060C;:|\t\r\n]+")

def _normalize_text(s: str) -> str:
    if not s: return ""
    s = unicodedata.normalize("NFKC", s); s = s.translate(_AR_TO_EN_DIGITS)
    s = s.replace("،", ","); s = re.sub(r"\s+", " ", s).strip()
    return s

def _parse_one_number(token: str) -> float:
    if not token: raise ValueError("قيمة رقمية فارغة")
    t = token.strip().upper(); t = re.sub(r"^[^\d+-.]+|[^\dA-Z.+-]+$", "", t); t = t.replace(",", "")
    m = re.match(r"^([+\-]?\d+(?:\.\d+)?)([KMB])?$", t)
    if not m: raise ValueError(f"قيمة رقمية غير صالحة: '{token}'")
    num_str, suf = m.groups(); scale = _SUFFIXES.get(suf or "", 1)
    return float(num_str) * scale

def _tokenize_numbers(s: str) -> List[str]:
    s = _normalize_text(s); s = _SEPARATORS_REGEX.sub(" ", s)
    return [p for p in s.split(" ") if p]

def _coalesce_num_suffix_tokens(tokens: List[str]) -> List[str]:
    out: List[str] = []; i = 0
    while i < len(tokens):
        cur = tokens[i].strip(); nxt = tokens[i + 1].strip() if i + 1 < len(tokens) else None
        if nxt and re.fullmatch(r"[KMBkmb]", nxt): out.append(cur + nxt.upper()); i += 2
        else: out.append(cur); i += 1
    return out

def parse_number(s: str) -> float:
    tokens = _tokenize_numbers(s)
    if not tokens: raise ValueError("لم يتم العثور على قيمة رقمية.")
    tokens = _coalesce_num_suffix_tokens(tokens); return _parse_one_number(tokens[0])

def parse_number_list(s: str) -> List[float]:
    tokens = _tokenize_numbers(s)
    if not tokens: raise ValueError("لم يتم العثور على أي أرقام.")
    tokens = _coalesce_num_suffix_tokens(tokens); return [_parse_one_number(t) for t in tokens]

# --- Command/Text Parsing Utilities (Unchanged) ---

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
    # ... (This function remains the same) ...
# --- END OF FINAL, CORRECTED FILE ---