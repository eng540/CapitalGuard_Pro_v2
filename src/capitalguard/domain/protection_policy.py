from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


class ProtectionPolicyError(ValueError):
    """Raised when a profit-protection policy is unsafe or internally inconsistent."""


@dataclass(frozen=True)
class ProtectionPolicy:
    mode: str
    active: bool
    side: str
    entry: Decimal | None = None
    stop_loss: Decimal | None = None
    trailing_value: Decimal | None = None
    profit_stop_price: Decimal | None = None
    break_even_after_profit_pct: Decimal | None = None
    break_even_buffer: Decimal = Decimal("0")

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "ProtectionPolicy":
        def decimal(name: str, default: Decimal | None = None) -> Decimal | None:
            value = record.get(name, default)
            if value is None:
                return None
            try:
                return Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ProtectionPolicyError(f"{name} must be numeric") from exc

        return cls(
            mode=str(record.get("profit_stop_mode", "NONE")).upper(),
            active=bool(record.get("profit_stop_active", False)),
            side=str(record.get("side", "LONG")).upper(),
            entry=decimal("entry"),
            stop_loss=decimal("stop_loss"),
            trailing_value=decimal("profit_stop_trailing_value"),
            profit_stop_price=decimal("profit_stop_price"),
            break_even_after_profit_pct=decimal("break_even_after_profit_pct"),
            break_even_buffer=decimal("break_even_buffer", Decimal("0")) or Decimal("0"),
        )

    def validate(self) -> None:
        if self.mode not in {"NONE", "FIXED", "TRAILING", "BREAK_EVEN", "TIME_BASED"}:
            raise ProtectionPolicyError(f"Unsupported protection mode: {self.mode}")
        if self.side not in {"LONG", "SHORT"}:
            raise ProtectionPolicyError("side must be LONG or SHORT")
        if not self.active or self.mode == "NONE":
            return
        if self.entry is None or self.entry <= 0:
            raise ProtectionPolicyError("entry must be positive for active protection")
        if self.stop_loss is None or self.stop_loss <= 0:
            raise ProtectionPolicyError("stop_loss must be positive for active protection")
        if self.side == "LONG" and self.stop_loss >= self.entry:
            raise ProtectionPolicyError("LONG stop_loss must be below entry")
        if self.side == "SHORT" and self.stop_loss <= self.entry:
            raise ProtectionPolicyError("SHORT stop_loss must be above entry")
        if self.mode == "TRAILING":
            if self.trailing_value is None or self.trailing_value <= 0:
                raise ProtectionPolicyError("TRAILING requires a positive trailing value")
        if self.mode == "FIXED":
            if self.profit_stop_price is None or self.profit_stop_price <= 0:
                raise ProtectionPolicyError("FIXED requires a positive profit stop price")
        if self.mode == "BREAK_EVEN":
            if self.break_even_after_profit_pct is None or self.break_even_after_profit_pct <= 0:
                raise ProtectionPolicyError("BREAK_EVEN requires a positive profit threshold")
            if self.break_even_buffer < 0:
                raise ProtectionPolicyError("break_even_buffer cannot be negative")

    def is_valid(self) -> bool:
        try:
            self.validate()
        except ProtectionPolicyError:
            return False
        return True
