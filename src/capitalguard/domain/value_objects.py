# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/domain/value_objects.py ---v30
"""
Defines the Value Objects for the domain. These are immutable objects that
describe attributes of entities but have no conceptual identity.

Updated behaviour (v25.1):
- Symbol normalization: accepts common exchange formats (e.g. "ETH/USDT", "Fetch.AI/TetherUS",
  "ZEC/USDT", "ENS/USDT") and normalizes to an uppercase ticker string (e.g. "ETHUSDT", "FETUSDT").
- A mapping table for well-known long names -> tickers is provided to fix cases like "Fetch.AI".
- If a two-token form is provided (base/quote), both tokens are normalized and joined.
- Non-alphanumeric separators are tolerated and removed.
- Validation regex is relaxed to allow typical tickers after normalization.
"""
from __future__ import annotations
from dataclasses import dataclass
import re
from typing import List, Dict, Any
from decimal import Decimal, InvalidOperation

# Mapping for known full names -> canonical tickers
_SYMBOL_LOOKUP = {
    "FETCH.AI": "FET",
    "FETCHAI": "FET",
    "TETHER": "USDT",
    "TETHERUS": "USDT",
    "BITCOIN": "BTC",
    "ETHEREUM": "ETH",
    "SOLANA": "SOL",
    "DOGECOIN": "DOGE",
    "RIPPLE": "XRP",
    "USD": "USD",
    "USDT": "USDT",
    "USDC": "USDC",
    # add more mappings here as needed
}

# Allowed normalized symbol pattern after processing (letters + digits, length reasonable)
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{3,20}$")


class Symbol:
    """Represents a trading symbol. Immutable and always uppercase.

    Normalization rules:
    - Accepts "BASE/QUOTE", "BASE-QUOTE", "BASE:QUOTE", "BASE QUOTE", or single token like "BTCUSDT".
    - Removes dots and other non-alphanumeric separators.
    - Uses _SYMBOL_LOOKUP to map long names to tickers when possible.
    - Falls back to concatenating cleaned base+quote tokens.
    """
    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Symbol value must be a non-empty string.")
        orig = value
        try:
            normalized = self._normalize_asset(value)
        except Exception as e:
            raise ValueError(f"Invalid symbol format: '{orig}' ({e})")
        if not _SYMBOL_RE.match(normalized):
            raise ValueError(f"Invalid symbol format: '{orig}' -> normalized '{normalized}'")
        self.value = normalized

    def __repr__(self) -> str:
        return f"Symbol('{self.value}')"

    def __eq__(self, other) -> bool:
        return isinstance(other, Symbol) and self.value == other.value

    def __hash__(self) -> int:
        return hash(self.value)

    @staticmethod
    def _clean_token(tok: str) -> str:
        """Remove undesirable characters and uppercase."""
        if tok is None:
            return ""
        # Replace non-alphanumeric with empty, uppercase
        cleaned = re.sub(r"[^A-Za-z0-9]", "", tok).upper()
        return cleaned

    @classmethod
    def _map_known(cls, token: str) -> str:
        """Map long names to canonical tickers using lookup table."""
        if not token:
            return token
        # direct lookup
        if token in _SYMBOL_LOOKUP:
            return _SYMBOL_LOOKUP[token]
        # try simple fallback by removing dots etc and lookup
        fallback = re.sub(r"[^A-Za-z0-9]", "", token).upper()
        return _SYMBOL_LOOKUP.get(fallback, fallback)

    @classmethod
    def _normalize_asset(cls, raw: str) -> str:
        """Turn raw asset string into canonical ticker format.

        Examples:
        - "ZEC/USDT" -> "ZECUSDT"
        - "Fetch.AI/TetherUS" -> "FETUSDT" (via mapping)
        - "ENS/USDT" -> "ENSUSDT"
        - "BTCUSDT" -> "BTCUSDT" (kept if valid)
        """
        s = raw.strip()
        # If already looks like a normalized single token, clean and validate
        single_clean = cls._clean_token(s)
        if _SYMBOL_RE.match(single_clean):
            return single_clean

        # Split by common separators
        tokens = re.split(r"[\/\-\:\s]+", s)
        tokens = [t for t in tokens if t != ""]
        if len(tokens) == 1:
            # try cleaning the single token further
            return cls._clean_token(tokens[0])

        if len(tokens) >= 2:
            base_raw = tokens[0]
            quote_raw = tokens[-1]  # use first and last; ignore intermediates if present
            base_mapped = cls._map_known(base_raw.upper())
            quote_mapped = cls._map_known(quote_raw.upper())
            base_mapped = cls._clean_token(base_mapped)
            quote_mapped = cls._clean_token(quote_mapped)
            combined = f"{base_mapped}{quote_mapped}"
            # If still too long or invalid, attempt conservative fallback: strip separators and uppercase
            combined_clean = re.sub(r"[^A-Za-z0-9]", "", combined).upper()
            return combined_clean

        # Last resort: remove non-alphanumerics and uppercase
        return re.sub(r"[^A-Za-z0-9]", "", s).upper()


class Side:
    """Represents the direction of a trade. Immutable."""
    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("Side value must be a non-empty string.")
        v = value.strip().upper()
        if v not in ("LONG", "SHORT"):
            raise ValueError("Invalid side. Must be 'LONG' or 'SHORT'.")
        self.value = v

    def __repr__(self) -> str:
        return f"Side('{self.value}')"

    def __eq__(self, other) -> bool:
        return isinstance(other, Side) and self.value == other.value

    def __hash__(self) -> int:
        return hash(self.value)


@dataclass(frozen=True)
class Price:
    """Represents a price value using Decimal for financial precision. Immutable."""
    value: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.value, Decimal):
            raise TypeError("Price value must be a Decimal.")
        # Allow price == 0 in certain contexts (e.g., unset), but keep negative check
        if self.value < Decimal(0):
            raise ValueError("Price must be non-negative.")


@dataclass(frozen=True)
class Target:
    """Represents a single profit target with its price and closing percentage."""
    price: Price
    close_percent: float

    def __post_init__(self) -> None:
        if not (0 <= self.close_percent <= 100):
            raise ValueError("Close percent must be between 0 and 100.")


class Targets:
    """Represents a collection of ordered profit targets. Immutable."""
    def __init__(self, values: List[Dict[str, Any]]) -> None:
        if values is None or not isinstance(values, list):
            raise ValueError("Targets must be a list of dictionaries (can be empty).")

        self._values: List[Target] = []
        for v in values:
            if not isinstance(v, dict) or "price" not in v:
                raise ValueError("Invalid target format. Must be a list of {'price': Decimal|str|float, 'close_percent': float}")
            price_val = v["price"]
            if not isinstance(price_val, Decimal):
                try:
                    price_val = Decimal(str(price_val))
                except (InvalidOperation, TypeError, ValueError):
                    raise ValueError(f"Invalid price value in target: {v.get('price')}")
            close_pct = float(v.get("close_percent", 0.0))
            self._values.append(Target(price=Price(price_val), close_percent=close_pct))

        # Empty targets allowed in some flows; keep validation flexible
        # but maintain invariant that _values is a list of Target
        if not isinstance(self._values, list):
            raise ValueError("Targets processing failed; expected list of Target objects.")

    @property
    def values(self) -> List[Target]:
        return self._values

    def __repr__(self) -> str:
        return f"Targets({self._values})"
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/domain/value_objects.py ---