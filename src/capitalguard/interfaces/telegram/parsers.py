# src/capitalguard/interfaces/telegram/parsers.py (v1.3.1 - Unified & Backward Compatible)
"""
Parsers for converting structured user text input into structured recommendation data.

✅ This version is fully compatible with conversation_handlers v35.1.
✅ Includes backward-compatible aliases for older handler imports.
✅ Provides type-safe parsing using Decimal for financial precision.
"""

import re
import logging
import warnings
from typing import Dict, Any, List, Optional
from decimal import Decimal, InvalidOperation

__version__ = "1.3.1"
__compatible_with__ = "conversation_handlers >= v35.0"

log = logging.getLogger(__name__)

# --- Localization & Normalization ---
_AR_TO_EN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_SUFFIXES = {"K": Decimal("1000"), "M": Decimal("1000000"), "B": Decimal("1000000000")}


def _normalize_text(s: str) -> str:
    """Normalize Arabic numerals and punctuation to English standard form."""
    if not s:
        return ""
    s = s.translate(_AR_TO_EN_DIGITS)
    s = s.replace("،", ",")
    return s.strip()


# --- Core Parsers ---

def parse_number(token: str) -> Optional[Decimal]:
    """Parse a single numeric token into a Decimal, supporting suffixes like K, M, B."""
    if not token:
        return None
    try:
        t = _normalize_text(token).upper().replace(",", "")
        multiplier = Decimal("1")
        num_part = t

        if t.endswith(tuple(_SUFFIXES.keys())):
            multiplier = _SUFFIXES[t[-1]]
            num_part = t[:-1]

        if not re.fullmatch(r"[+\-]?\d+(\.\d+)?", num_part):
            return None

        return Decimal(num_part) * multiplier
    except (InvalidOperation, TypeError):
        return None


def parse_targets_list(tokens: List[str]) -> List[Dict[str, Any]]:
    """Parse targets like ['60k@50', '62k@50'] into [{'price': Decimal, 'close_percent': float}]."""
    parsed_targets = []
    for token in tokens:
        price_str, close_pct_str = token, "0"
        if '@' in token:
            parts = token.split('@', 1)
            if len(parts) != 2:
                continue
            price_str, close_pct_str = parts[0], parts[1]

        price = parse_number(price_str)
        close_pct = parse_number(close_pct_str) if close_pct_str else Decimal("0")

        if price is not None and close_pct is not None:
            parsed_targets.append({"price": price, "close_percent": float(close_pct)})

    if not parsed_targets and tokens:
        for token in tokens:
            if price := parse_number(token):
                parsed_targets.append({"price": price, "close_percent": 0.0})

    if parsed_targets and all(t["close_percent"] == 0.0 for t in parsed_targets):
        parsed_targets[-1]["close_percent"] = 100.0

    return parsed_targets


def parse_rec_command(text: str) -> Optional[Dict[str, Any]]:
    """Parse a quick-style command like:
    'BTCUSDT LONG 59k 58k 60k@50 62k@50'
    """
    try:
        parts = _normalize_text(text).split()
        if not parts or len(parts) < 5:
            return None

        asset = parts[0].upper()
        side = parts[1].upper()
        entry = parse_number(parts[2])
        stop_loss = parse_number(parts[3])
        target_tokens = parts[4:]

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
        log.error(f"Error parsing rec command: {e}")
        return None


def parse_editor_command(text: str) -> Optional[Dict[str, Any]]:
    """Parse text-editor style recommendation with key:value lines."""
    data: Dict[str, Any] = {}
    key_map = {
        "asset": ["asset", "symbol"],
        "side": ["side", "type"],
        "entry": ["entry"],
        "stop_loss": ["stop_loss", "stop", "sl"],
        "targets": ["targets", "tps"],
        "market": ["market"],
        "notes": ["notes", "note"],
    }
    reverse_key_map = {alias.lower(): key for key, aliases in key_map.items() for alias in aliases}

    for raw_line in text.strip().split("\n"):
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

    if not all(k in data for k in ["asset", "side", "entry", "stop_loss", "targets"]):
        return None

    data.setdefault("market", "Futures")
    data.setdefault("order_type", "LIMIT")
    data.setdefault("notes", None)

    return data


# --- Backward Compatibility Layer (for conversation_handlers v35.x) ---

def parse_quick_command(text: str):
    """Deprecated alias for backward compatibility with older handlers."""
    warnings.warn(
        "parse_quick_command() is deprecated. Use parse_rec_command() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return parse_rec_command(text)


def parse_text_editor(text: str):
    """Deprecated alias for backward compatibility with older handlers."""
    warnings.warn(
        "parse_text_editor() is deprecated. Use parse_editor_command() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return parse_editor_command(text)