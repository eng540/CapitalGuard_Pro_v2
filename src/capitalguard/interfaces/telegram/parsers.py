# --- START OF FINAL, RE-ARCHITECTED, AND READY-TO-USE FILE: src/capitalguard/interfaces/telegram/parsers.py ---
import re
import unicodedata
from typing import Dict, Any, List, Optional
import logging

log = logging.getLogger(__name__)

# Centralized normalization and number parsing helpers
_AR_TO_EN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_SUFFIXES = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}

# Treat common separators (including Arabic comma) as whitespace
_SEPARATORS_REGEX = re.compile(r"[,\u060C;:|/\\\t\r\n]+")

# Any junk letters between two digits (e.g., "250b260") become a separator.
# We allow only K/M/B/@/./+/- to remain attached to numbers.
_BETWEEN_DIGITS_JUNK = re.compile(r"(?<=\d)[^0-9KMB@.+-]+(?=\d)", re.IGNORECASE)

def _normalize_text(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_AR_TO_EN_DIGITS)      # Arabic-Indic digits -> Latin
    s = s.replace("،", ",")                # Arabic comma -> comma
    s = _SEPARATORS_REGEX.sub(" ", s)      # unify basic separators
    s = _BETWEEN_DIGITS_JUNK.sub(" ", s)   # fix cases like "250b260" -> "250 260"
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _parse_one_number(token: str) -> float:
    if not token:
        raise ValueError("قيمة رقمية فارغة")
    t = token.strip().upper()
    # Trim non-numeric leading/trailing chars except valid ones
    t = re.sub(r"^[^\d+-.]+|[^\dA-Z.+-]+$", "", t)
    t = t.replace(",", "")
    m = re.match(r"^([+\-]?\d+(?:\.\d+)?)([KMB])?$", t)
    if not m:
        raise ValueError(f"قيمة رقمية غير صالحة: '{token}'")
    num_str, suf = m.groups()
    scale = _SUFFIXES.get(suf or "", 1)
    return float(num_str) * scale

def parse_number(s: str) -> float:
    s = _normalize_text(s)
    tokens = [p for p in s.split(" ") if p]
    if not tokens:
        raise ValueError("لم يتم العثور على قيمة رقمية.")
    return _parse_one_number(tokens[0])

def parse_targets_list(tokens: List[str]) -> List[Dict[str, float]]:
    """
    Parses target tokens into a list of dicts: [{"price": float, "close_percent": float}, ...]
    Supports "price" or "price@percent" formats, with K/M/B suffixes and Arabic digits.
    If no percentages are provided at all, the last target defaults to 100%.
    """
    parsed: List[Dict[str, float]] = []
    for token in tokens:
        token = _normalize_text(token)
        if not token:
            continue
        if '@' in token:
            left, right = token.split('@', 1)
            price = _parse_one_number(left)
            pct = _parse_one_number(right)
            parsed.append({"price": price, "close_percent": pct})
        else:
            price = _parse_one_number(token)
            parsed.append({"price": price, "close_percent": 0.0})
    if parsed and all(t["close_percent"] == 0.0 for t in parsed):
        parsed[-1]["close_percent"] = 100.0
        log.debug("No partial close %% defined; last target set to 100%%.")
    return parsed

# --- High-level parsers for Telegram inputs ---

def parse_quick_command(text: str) -> Optional[Dict[str, Any]]:
    """
    Expected minimal format (flexible spacing/separators):
      /rec <ASSET> <LONG|SHORT> <ENTRY> <STOP> <TARGETS...>
    TARGETS can be like: 116k 117k@50 118k@50
    """
    try:
        raw = _normalize_text(text or "")
        if not raw.lower().startswith("/rec "):
            return None
        parts = raw.split(" ")
        # parts: ["/rec", ASSET, SIDE, ENTRY, STOP, TARGET1, TARGET2, ...]
        if len(parts) < 5:
            return None
        _, asset, side, entry_s, stop_s, *target_tokens = parts
        asset = asset.upper()
        side = side.upper()
        entry = _parse_one_number(entry_s)
        stop_loss = _parse_one_number(stop_s)
        targets = parse_targets_list(target_tokens)
        if not targets:
            raise ValueError("At least one target is required.")
        return {
            "asset": asset,
            "side": side,
            "entry": entry,
            "stop_loss": stop_loss,
            "targets": targets,
            "market": "Futures",
            "order_type": "LIMIT",
        }
    except (ValueError, IndexError) as e:
        log.error("Error parsing quick command: %s", e)
        return None

def parse_text_editor(text: str) -> Optional[Dict[str, Any]]:
    """
    Parses a field-based text. Supports multiple blocks; the last occurrence of a key wins.
    Keys (case-insensitive): asset/symbol, side/type, entry, stop/sl, targets/tps, market
    Targets support "price" or "price@percent".
    """
    data: Dict[str, Any] = {}
    key_map = {
        'asset': ['asset', 'symbol', 'الأصل'],
        'side': ['side', 'type', 'direction', 'الاتجاه'],
        'entry': ['entry', 'entries', 'دخول'],
        'stop_loss': ['stop', 'sl', 'stoploss', 'إيقاف', 'وقف'],
        'targets': ['targets', 'tps', 'goals', 'أهداف'],
        'notes': ['notes', 'note', 'ملاحظات'],
        'market': ['market', 'سوق'],
        'risk': ['risk', 'مخاطرة'],
        'order_type': ['order_type', 'ordertype', 'نوع_الأمر'],
    }

    # Normalize line endings, keep last value per key if repeated
    lines = (text or "").splitlines()
    for line in lines:
        if ":" not in line:
            continue
        k_raw, v_raw = line.split(":", 1)
        k = _normalize_text(k_raw).lower()
        v = v_raw.strip()
        if not v:
            continue
        for key, aliases in key_map.items():
            if k in (a.lower() for a in aliases):
                if key == "targets":
                    # split on spaces after normalization; handle junk like "250b260"
                    v_norm = _normalize_text(v)
                    target_tokens = [t for t in v_norm.split(" ") if t]
                    data[key] = parse_targets_list(target_tokens)
                elif key in ("entry", "stop_loss"):
                    data[key] = parse_number(v)
                elif key in ("asset", "side", "market", "order_type"):
                    data[key] = _normalize_text(v).upper()
                else:
                    data[key] = v.strip()
                break

    if not all(k in data for k in ("asset", "side", "entry", "stop_loss", "targets")):
        return None

    # Defaults
    data.setdefault("market", "Futures")
    data.setdefault("order_type", "LIMIT")
    return data
# --- END OF FINAL, RE-ARCHITECTED, AND READY-TO-USE FILE: src/capitalguard/interfaces/telegram/parsers.py ---