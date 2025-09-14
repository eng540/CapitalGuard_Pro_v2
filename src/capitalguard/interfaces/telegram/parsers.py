# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
import re
import unicodedata
from typing import Dict, Any, List, Optional
import logging

log = logging.getLogger(__name__)

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
    return float(num_str)

def parse_number(s: str) -> float:
    s = _normalize_text(s)
    tokens = [p for p in s.split(" ") if p]
    if not tokens: raise ValueError("لم يتم العثور على قيمة رقمية.")
    return _parse_one_number(tokens[0])

def parse_targets_list(tokens: List[str]) -> List[Dict[str, float]]:
    """
    Analyzes a list of string tokens and converts them into a list of target dictionaries.
    Supports both "price" and "price@percent" formats.
    """
    parsed_targets = []
    for token in tokens:
        token = token.strip()
        if not token: continue
        
        if '@' in token:
            parts = token.split('@', 1)
            if len(parts) != 2:
                raise ValueError(f"تنسيق الهدف غير صالح: '{token}'")
            price = _parse_one_number(parts[0])
            percent = _parse_one_number(parts[1])
            parsed_targets.append({"price": price, "close_percent": percent})
        else:
            price = _parse_one_number(token)
            parsed_targets.append({"price": price, "close_percent": 0.0})
            
    if all(t['close_percent'] == 0.0 for t in parsed_targets) and parsed_targets:
        parsed_targets[-1]['close_percent'] = 100.0
        log.debug("No partial close % defined, setting last target to 100% close.")

    return parsed_targets

def parse_quick_command(text: str) -> Optional[Dict[str, Any]]:
    try:
        parts = text.strip().split()
        if len(parts) < 5 or not parts[0].lower().startswith('/rec'):
            return None
            
        asset = parts[1].upper()
        side = parts[2].upper()
        entry = _parse_one_number(parts[3])
        stop_loss = _parse_one_number(parts[4])
        target_tokens = parts[5:]
        
        targets = parse_targets_list(target_tokens)
        if not targets: raise ValueError("At least one target is required.")

        return {
            "asset": asset, "side": side, "entry": entry,
            "stop_loss": stop_loss, "targets": targets,
            "market": "Futures", "order_type": "LIMIT"
        }
    except (ValueError, IndexError) as e:
        log.error(f"Error parsing quick command: {e}")
        return None

def parse_text_editor(text: str) -> Optional[Dict[str, Any]]:
    data = {}
    key_map = {
        'asset': ['asset', 'symbol'], 'side': ['side', 'type'],
        'entry': ['entry'], 'stop_loss': ['stop', 'sl'],
        'targets': ['targets', 'tps'], 'market': ['market']
    }
    for line in text.strip().split('\n'):
        try:
            key_str, value_str = line.split(':', 1)
            key_str, value_str = key_str.strip().lower(), value_str.strip()
            for key, aliases in key_map.items():
                if key_str in aliases:
                    if key == 'targets':
                        target_tokens = value_str.split()
                        data[key] = parse_targets_list(target_tokens)
                    elif key in ['entry', 'stop_loss']:
                        data[key] = _parse_one_number(value_str)
                    else:
                        data[key] = value_str.upper()
                    break
        except ValueError: continue
        
    if not all(k in data for k in ['asset', 'side', 'entry', 'stop_loss', 'targets']):
        return None
        
    data.setdefault('market', 'Futures')
    data.setdefault('order_type', 'LIMIT')
    return data
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---```