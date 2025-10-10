# src/capitalguard/interfaces/telegram/parsers.py (v1.1 - COMPLETE, FINAL & ARCHITECTURALLY-CORRECT)
"""
Parsers for converting structured user text input from conversations into data.

This module is kept separate to maintain a clear separation of concerns. It is
responsible only for parsing structured inputs that are expected from known
conversation steps (e.g., a reply containing only prices). For unstructured
text analysis (e.g., from forwarded messages), the ImageParsingService should be used.

This is a complete, final, and production-ready file.
"""

import re
from typing import List, Optional, Dict

def parse_number(token: str) -> Optional[float]:
    """
    Parses a single numeric token from a string.

    This function is designed to be flexible, supporting various formats:
    - Standard numbers: "123.45"
    - Suffix multipliers (case-insensitive): "50k" -> 50000, "1.5m" -> 1500000
    - Commas as thousand separators: "1,250.50"

    Args:
        token: The string token to parse.

    Returns:
        The parsed number as a float, or None if parsing fails.
    """
    if not token: 
        return None
    try:
        token_upper = token.strip().upper().replace(',', '')
        multipliers = {'K': 1000, 'M': 1000000}
        
        # Check if the last character is a known multiplier
        if token_upper.endswith(('K', 'M')):
            # Extract the numeric part and the multiplier
            number_part = token_upper[:-1]
            multiplier = multipliers[token_upper[-1]]
            return float(number_part) * multiplier
        
        # If no multiplier, parse as a standard float
        return float(token_upper)
    except (ValueError, TypeError):
        # Return None for any parsing errors to be handled by the caller
        return None

def parse_targets_list(tokens: List[str]) -> List[Dict[str, float]]:
    """
    Parses a list of string tokens into a structured list of take-profit targets.

    This function handles two primary syntaxes for each token:
    1. Simple price: "50000", "52k"
    2. Price with partial close percentage: "55000@50", "60k@25" (price@percentage)

    If no partial close percentages are provided at all, it automatically assumes
    the final target is a 100% close, which is a common trading convention.

    Args:
        tokens: A list of string tokens representing the targets.

    Returns:
        A list of dictionaries, where each dictionary represents a target
        with "price" and "close_percent" keys.
    """
    parsed_targets = []
    for token in tokens:
        token = token.strip()
        if not token: 
            continue

        price_str, close_pct_str = token, "0"
        if '@' in token:
            parts = token.split('@', 1)
            if len(parts) != 2: 
                continue  # Ignore malformed partial close tokens like "50@k@"
            price_str, close_pct_str = parts[0], parts[1]

        price = parse_number(price_str)
        # Use parse_number for close_pct as well, in case of formats like "50k@0.5k" (though unlikely)
        close_pct = parse_number(close_pct_str) if close_pct_str else 0.0
        
        if price is not None:
            parsed_targets.append({"price": price, "close_percent": close_pct or 0.0})

    # Business Rule: If a user provides targets like "50 52 55", they implicitly
    # mean that the position should be fully closed at the final target.
    if parsed_targets and all(t['close_percent'] == 0.0 for t in parsed_targets):
        parsed_targets[-1]['close_percent'] = 100.0
        
    return parsed_targets