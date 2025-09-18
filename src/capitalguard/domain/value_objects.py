# --- START OF FINAL, CONFIRMED AND PRODUCTION-READY FILE (Version 8.1.3) ---
# src/capitalguard/domain/value_objects.py

from __future__ import annotations
from dataclasses import dataclass
import re
from typing import List, Dict

class Symbol:
    def __init__(self, value: str) -> None:
        v = (value or "").strip().upper()
        if not re.match(r"^[A-Z0-9._:-]{3,30}$", v):
            raise ValueError("Invalid symbol")
        self.value = v

class Side:
    def __init__(self, value: str) -> None:
        v = (value or "").strip().upper()
        if v not in ("LONG", "SHORT"):
            raise ValueError("Invalid side (use LONG/SHORT)")
        self.value = v

@dataclass
class Price:
    value: float
    def __post_init__(self) -> None:
        try:
            self.value = float(self.value)
        except Exception:
            raise ValueError("Invalid price")
        if self.value <= 0:
            raise ValueError("Price must be > 0")

@dataclass
class Target:
    """Represents a single profit target with its price and closing percentage."""
    price: float
    close_percent: float

class Targets:
    def __init__(self, values: List[Dict[str, float]] | List[float]) -> None:
        if not values or not isinstance(values, list):
            raise ValueError("targets must be a non-empty list")
        
        self.values: List[Target] = []
        if all(isinstance(v, (int, float)) for v in values):
            total_targets = len(values)
            for i, v in enumerate(values):
                close_pct = 100.0 if i == total_targets - 1 else 0.0
                self.values.append(Target(price=float(v), close_percent=close_pct))
        else:
            for v in values:
                if not isinstance(v, dict) or "price" not in v or "close_percent" not in v:
                    raise ValueError("Invalid target format. Must be a list of {'price': float, 'close_percent': float}")
                self.values.append(Target(price=float(v["price"]), close_percent=float(v["close_percent"])))

# --- END OF FINAL, CONFIRMED AND PRODUCTION-READY FILE (Version 8.1.3) ---