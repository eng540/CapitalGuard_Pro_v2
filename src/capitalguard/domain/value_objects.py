# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass
import re
from typing import List

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

class Targets:
    def __init__(self, values: List[float]) -> None:
        if not values or not isinstance(values, list):
            raise ValueError("targets must be a non-empty list")
        self.values = [float(v) for v in values]