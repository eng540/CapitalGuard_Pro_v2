# src/capitalguard/interfaces/telegram/parsers.py (v1.6.0 - Production Ready & Final)
"""
Robust and production-ready text parsers for handling user input.
- Handles Arabic and English numerals.
- Supports financial suffixes (K, M, B).
- Safely parses complex target lists (e.g., price@percentage).
- Includes a flexible parser for trailing stop distances (% or absolute value).
- All functions are designed to be safe and never crash on invalid input.
"""

import re
import logging
from typing import Dict, Any, List, Optional, Union
from decimal import Decimal, InvalidOperation

log = logging.getLogger(__name__)

# --- Constants and Normalization ---
_AR_TO_EN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_SUFFIXES = {"K": Decimal("1000"), "M": Decimal("1000000"), "B": Decimal("1000000000")}

def _normalize_text(s: str) -> str:
    """Normalizes Arabic numerals and symbols to their English counterparts."""
    if not s:
        return ""
    s = s.translate(_AR_TO_EN_DIGITS)
    s = s.replace("،", ",").replace("؛", ";").replace("؟", "?")
    s = re.sub(r'\s+', ' ', s.strip())
    return s

# --- Core Parsers ---

def parse_number(token: str) -> Optional[Decimal]:
    """
    Parses a single numeric token into a Decimal, supporting suffixes like K, M, B.
    Returns None if the token is invalid or represents a non-positive number.
    """
    if not token:
        return None
        
    try:
        t = _normalize_text(token).upper().replace(",", "").replace(" ", "")
        multiplier = Decimal("1")
        num_part = t

        if t.endswith(tuple(_SUFFIXES.keys())):
            multiplier = _SUFFIXES[t[-1]]
            num_part = t[:-1]

        if not re.fullmatch(r"[+\-]?\d*\.?\d+", num_part):
            return None

        result = Decimal(num_part) * multiplier
        return result if result.is_finite() and result > 0 else None
        
    except (InvalidOperation, TypeError, ValueError) as e:
        log.debug(f"Failed to parse number: '{token}', error: {e}")
        return None

def parse_targets_list(tokens: List[str]) -> List[Dict[str, Any]]:
    """
    Parses a list of target tokens, e.g., ['60000@50', '62000'].
    Assigns 100% close percentage to the last target if no percentages are specified.
    """
    parsed_targets = []
    if not tokens:
        return parsed_targets
        
    for token in tokens:
        if not token or not token.strip():
            continue
            
        try:
            price_str, close_pct_str = token, "0"
            if '@' in token:
                parts = token.split('@', 1)
                if len(parts) != 2:
                    log.warning(f"Skipping malformed target token: {token}")
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
            log.warning(f"Failed to parse target token: '{token}', error: {e}")
            continue

    if parsed_targets and all(t["close_percent"] == 0.0 for t in parsed_targets):
        parsed_targets[-1]["close_percent"] = 100.0

    return parsed_targets

def parse_trailing_distance(input_str: str) -> Optional[Dict[str, Union[str, Decimal]]]:
    """
    Parses a trailing stop distance string like "2%" or "500".
    Returns a dictionary specifying the type ('percentage' or 'price_distance') and the value.
    """
    normalized = _normalize_text(input_str).strip().upper()
    if not normalized:
        return None

    if normalized.endswith('%'):
        try:
            value = Decimal(normalized[:-1])
            if 0 < value < 100:
                return {"type": "percentage", "value": value}
        except (InvalidOperation, TypeError, ValueError):
            return None
    else:
        try:
            value = Decimal(normalized)
            if value > 0:
                return {"type": "price_distance", "value": value}
        except (InvalidOperation, TypeError, ValueError):
            return None
    
    return None

def parse_rec_command(text: str) -> Optional[Dict[str, Any]]:
    """Parses a quick recommendation command string."""
    try:
        normalized_text = _normalize_text(text)
        parts = normalized_text.split()
        
        if not parts or len(parts) < 5:
            return None

        asset = parts[0].upper()
        side = parts[1].upper()
        
        if side not in ["LONG", "SHORT"]:
            return None
            
        entry = parse_number(parts[2])
        stop_loss = parse_number(parts[3])
        target_tokens = parts[4:]

        targets = parse_targets_list(target_tokens)
        if not all([asset, side, entry, stop_loss, targets]):
            return None

        return {
            "asset": asset, "side": side, "entry": entry,
            "stop_loss": stop_loss, "targets": targets,
            "market": "Futures", "order_type": "LIMIT",
        }
    except (ValueError, IndexError, TypeError) as e:
        log.error(f"Error parsing rec command: '{text}'. Error: {e}")
        return None

def parse_editor_command(text: str) -> Optional[Dict[str, Any]]:
    """Parses a key:value formatted text block."""
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

    for raw_line in text.strip().split("\n"):
        line = _normalize_text(raw_line)
        if ":" not in line:
            continue

        try:
            key_str, value_str = line.split(":", 1)
            key_str, value_str = key_str.strip().lower(), value_str.strip()

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

    required_keys = ["asset", "side", "entry", "stop_loss", "targets"]
    if not all(data.get(k) for k in required_keys):
        return None

    data.setdefault("market", "Futures")
    data.setdefault("order_type", "LIMIT")
    return data