# src/capitalguard/domain/value_objects.py (v25.0 - FINAL & UNIFIED)
"""
Defines the Value Objects for the domain. These are immutable objects that
describe attributes of entities but have no conceptual identity.
"""

from __future__ import annotations
from dataclasses import dataclass
import re
from typing import List, Dict
from decimal import Decimal, InvalidOperation

class Symbol:
    """Represents a trading symbol. Immutable and always uppercase."""
    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("Symbol value must be a non-empty string.")
        v = value.strip().upper()
        if not re.match(r"^[A-Z0-9._:-]{3,30}$", v):
            raise ValueError(f"Invalid symbol format: '{value}'")
        self.value = v
    def __repr__(self) -> str:
        return f"Symbol('{self.value}')"
    def __eq__(self, other) -> bool:
        return isinstance(other, Symbol) and self.value == other.value
    def __hash__(self) -> int:
        return hash(self.value)

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
        if self.value <= Decimal(0):
            raise ValueError("Price must be positive.")

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
    def __init__(self, values: List[Dict[str, any]]) -> None:
        if not values or not isinstance(values, list):
            raise ValueError("Targets must be a non-empty list of dictionaries.")
        
        self._values: List[Target] = []
        for v in values:
            if not isinstance(v, dict) or "price" not in v:
                raise ValueError("Invalid target format. Must be a list of {'price': Decimal, 'close_percent': float}")
            
            price_val = v["price"]
            if not isinstance(price_val, Decimal):
                try:
                    price_val = Decimal(str(price_val))
                except InvalidOperation:
                    raise ValueError(f"Invalid price value in target: {v['price']}")

            self._values.append(Target(
                price=Price(price_val),
                close_percent=float(v.get("close_percent", 0.0))
            ))
        
        if not self._values:
            raise ValueError("Targets list cannot be empty after processing.")

    @property
    def values(self) -> List[Target]:
        return self._values

    def __repr__(self) -> str:
        return f"Targets({self._values})"