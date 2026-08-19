"""Conservative consistency checks for historical parsed signals."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ConsistencyReport:
    is_consistent: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


class FinancialConsistencyService:
    @staticmethod
    def _decimal(value: Any) -> Decimal | None:
        try:
            result = Decimal(str(value))
            return result if result.is_finite() and result > 0 else None
        except (ArithmeticError, TypeError, ValueError):
            return None

    def check(self, *, side: str | None, entry: Any, stop_loss: Any, targets: list[dict[str, Any]] | None) -> ConsistencyReport:
        errors: list[str] = []
        warnings: list[str] = []
        normalized_side = str(side or "").upper()
        entry_value = self._decimal(entry)
        stop_value = self._decimal(stop_loss)
        target_values = [self._decimal(item.get("price")) for item in (targets or []) if isinstance(item, dict)]
        target_values = [value for value in target_values if value is not None]

        if normalized_side not in {"LONG", "SHORT"}:
            errors.append("SIDE_INVALID")
        if entry_value is None:
            errors.append("ENTRY_INVALID")
        if stop_value is None:
            errors.append("STOP_INVALID")
        if not target_values:
            errors.append("TARGETS_INVALID")
        if errors:
            return ConsistencyReport(False, tuple(errors), tuple(warnings))

        assert entry_value is not None
        assert stop_value is not None
        if normalized_side == "LONG":
            if stop_value >= entry_value:
                errors.append("LONG_STOP_MUST_BE_BELOW_ENTRY")
            if any(target <= entry_value for target in target_values):
                errors.append("LONG_TARGET_MUST_BE_ABOVE_ENTRY")
            if target_values != sorted(target_values):
                errors.append("LONG_TARGETS_NOT_ASCENDING")
        else:
            if stop_value <= entry_value:
                errors.append("SHORT_STOP_MUST_BE_ABOVE_ENTRY")
            if any(target >= entry_value for target in target_values):
                errors.append("SHORT_TARGET_MUST_BE_BELOW_ENTRY")
            if target_values != sorted(target_values, reverse=True):
                errors.append("SHORT_TARGETS_NOT_DESCENDING")

        percentages = [
            Decimal(str(item.get("close_percent")))
            for item in (targets or [])
            if isinstance(item, dict) and item.get("close_percent") is not None
        ]
        if percentages:
            if any(value < 0 or value > 100 for value in percentages):
                errors.append("TARGET_CLOSE_PERCENT_INVALID")
            elif sum(percentages) > Decimal("100.0001"):
                errors.append("TARGET_CLOSE_PERCENT_OVER_100")
            elif sum(percentages) < Decimal("99.9999"):
                warnings.append("TARGET_CLOSE_PERCENT_BELOW_100")

        return ConsistencyReport(not errors, tuple(errors), tuple(warnings))
