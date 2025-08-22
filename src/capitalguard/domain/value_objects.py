from dataclasses import dataclass
from enum import Enum
from typing import List

class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    SPOT = "SPOT"

@dataclass(frozen=True)
class Symbol:
    value: str
    def __post_init__(self):
        v = self.value.strip().upper()
        if not v:
            raise ValueError("Symbol cannot be empty")
        object.__setattr__(self, "value", v)

@dataclass(frozen=True)
class Price:
    value: float
    def __post_init__(self):
        if self.value <= 0:
            raise ValueError("Price must be positive")

@dataclass(frozen=True)
class Targets:
    values: List[float]
    def __post_init__(self):
        if not self.values:
            raise ValueError("At least one target is required")
        if any(v <= 0 for v in self.values):
            raise ValueError("Targets must be positive")
