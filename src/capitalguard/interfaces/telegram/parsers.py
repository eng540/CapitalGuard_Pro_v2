# --- START OF FULL, FINAL, AND ENHANCED FILE ---
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
    if not token:
        raise ValueError("Empty numeric value")
    t = token.strip().upper()
    t = re.sub(r"^[^\d+-.]+|[^\dA-Z.+-]+$", "", t)
    t = t.replace(",", "")
    m = re.match(r"^([+\-]?\d+(?:\.\d+)?)([KMB])?$", t)
    if not m:
        raise ValueError(f"Invalid numeric value: '{token}'")
    num_str, suf = m.groups()
    scale = _SUFFIXES.get(suf or "", 1)
    return float(num_str) * scale

def parse_number(s: str) -> float:
    s = _normalize_text(s)
    tokens = [p for p in s.split(" ") if p]
    if not tokens:
        raise ValueError("No numeric value found.")
    return _parse_one_number(tokens[0])

def parse_targets_list(tokens: List[str]) -> List[Dict[str, float]]:
    parsed_targets = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue

        if '@' in token:
            parts = token.split('@', 1)
            if len(parts) != 2:
                raise ValueError(f"Invalid target format: '{token}'")
            price = _parse_one_number(parts[0])
            percent = _parse_one_number(parts[1])
            parsed_targets.append({"price": price, "close_percent": percent})
        else:
            price = _parse_one_number(token)
            parsed_targets.append({"price": price, "close_percent": 0.0})

    if parsed_targets and all(t['close_percent'] == 0.0 for t in parsed_targets):
        parsed_targets[-1]['close_percent'] = 100.0
        log.debug("No partial close % defined, setting last target to 100% close.")

    return parsed_targets

def parse_quick_command(text: str) -> Optional[Dict[str, Any]]:
    try:
        parts = _normalize_text(text).split()
        command_index = -1
        for i, part in enumerate(parts):
            if part.lower().startswith('/rec'):
                command_index = i
                break
        
        if command_index == -1 or len(parts) < command_index + 5:
            return None

        content_parts = parts[command_index + 1:]
        
        asset = content_parts[0].upper()
        side = content_parts[1].upper()
        entry = _parse_one_number(content_parts[2])
        stop_loss = _parse_one_number(content_parts[3])
        target_tokens = content_parts[4:]

        targets = parse_targets_list(target_tokens)
        if not targets:
            raise ValueError("At least one target is required.")

        return {
            "asset": asset, "side": side, "entry": entry,
            "stop_loss": stop_loss, "targets": targets,
            "market": "Futures", "order_type": "LIMIT",
        }
    except (ValueError, IndexError) as e:
        log.error(f"Error parsing quick command: {e}")
        return None

# ✅ ENHANCED: This version correctly implements the original designer's intent for flexibility.
def parse_text_editor(text: str) -> Optional[Dict[str, Any]]:
    """
    Parses text editor format, supporting aliases for keys.
      asset: BTCUSDT
      side: LONG
      entry: 59k
      sl: 58k
      tps: 60k@30 62k@50
      market: Futures
      notes: Some notes here
    """
    data: Dict[str, Any] = {}
    key_map = {
        'asset': ['asset', 'symbol'],
        'side': ['side', 'type'],
        'entry': ['entry'],
        'stop_loss': ['stop_loss', 'stop', 'sl'], # Correctly includes the primary key
        'targets': ['targets', 'tps'],
        'market': ['market'],
        'notes': ['notes', 'note'],
    }
    
    # Create a reverse map for quick and robust lookup: {'symbol': 'asset', 'sl': 'stop_loss', ...}
    reverse_key_map = {alias.lower(): key for key, aliases in key_map.items() for alias in aliases}

    for raw_line in text.strip().split('\n'):
        line = _normalize_text(raw_line)
        if ':' not in line:
            continue
        
        try:
            key_str, value_str = line.split(':', 1)
            key_str = key_str.strip().lower()
            value_str = value_str.strip()

            if key_str in reverse_key_map:
                main_key = reverse_key_map[key_str]
                
                if main_key == 'targets':
                    data[main_key] = parse_targets_list(value_str.split())
                elif main_key in ['entry', 'stop_loss']:
                    data[main_key] = _parse_one_number(value_str)
                elif main_key in ['asset', 'side', 'market']:
                    data[main_key] = value_str.upper()
                else: # Handles 'notes' and any other future text fields
                    data[main_key] = value_str

        except (ValueError, IndexError) as e:
            log.warning(f"Could not parse line in text editor mode: '{raw_line}'. Error: {e}")
            continue

    # Check for mandatory fields
    if not all(k in data for k in ['asset', 'side', 'entry', 'stop_loss', 'targets']):
        log.error(f"Missing mandatory fields in text editor input. Found: {data.keys()}")
        return None

    # Set defaults for optional fields
    data.setdefault('market', 'Futures')
    data.setdefault('order_type', 'LIMIT')
    data.setdefault('notes', None)
    
    return data

# --- END OF FULL, FINAL, AND ENHANCED FILE ---