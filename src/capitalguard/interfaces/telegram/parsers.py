# src/capitalguard/interfaces/telegram/parsers.py (v1.2 - COMPLETE, FINAL & TYPE-SAFE)
"""
Parsers for converting structured user text input from conversations into data.
This version has been upgraded to work exclusively with Decimals for type safety
and financial precision, resolving conversion and validation errors.
"""

import re
from typing import List, Optional, Dict
from decimal import Decimal, InvalidOperation

def parse_number(token: str) -> Optional[Decimal]:
    """
    Parses a single numeric token from a string into a Decimal object.
    Supports suffixes like 'k' and 'm'.
    """
    if not token: 
        return None
    try:
        token_upper = token.strip().upper().replace(',', '')
        multipliers = {'K': Decimal('1000'), 'M': Decimal('1000000')}
        
        number_part_str = token_upper
        multiplier = Decimal('1')

        if token_upper.endswith(('K', 'M')):
            number_part_str = token_upper[:-1]
            multiplier = multipliers[token_upper[-1]]
        
        # Ensure the string is a valid number before converting
        if not re.fullmatch(r'[+\-]?\d+(\.\d+)?', number_part_str):
            return None

        return Decimal(number_part_str) * multiplier
    except (InvalidOperation, TypeError):
        return None

def parse_targets_list(tokens: List[str]) -> List[Dict[str, any]]:
    """
    Parses a list of string tokens into a structured list of take-profit targets,
    returning Decimal objects for prices and float for percentages.
    """
    parsed_targets = []
    for token in tokens:
        token = token.strip()
        if not token: 
            continue

        price_str, close_pct_str = token, "0"
        if '@' in token:
            parts = token.split('@', 1)
            if len(parts) != 2: continue
            price_str, close_pct_str = parts[0], parts[1]

        price = parse_number(price_str)
        close_pct = parse_number(close_pct_str) if close_pct_str else Decimal('0')
        
        if price is not None and close_pct is not None:
            parsed_targets.append({"price": price, "close_percent": float(close_pct)})

    if not parsed_targets and tokens:
        # This fallback ensures simple lists like "10 11 12" work reliably.
        for token in tokens:
            if price := parse_number(token):
                parsed_targets.append({"price": price, "close_percent": 0.0})

    if parsed_targets and all(t['close_percent'] == 0.0 for t in parsed_targets):
        parsed_targets[-1]['close_percent'] = 100.0
        
    return parsed_targets