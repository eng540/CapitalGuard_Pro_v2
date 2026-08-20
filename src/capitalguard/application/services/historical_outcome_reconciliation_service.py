from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


@dataclass(frozen=True)
class ReportedOutcomeReport:
    status: str
    derived_pnl_pct: Decimal | None
    reported_pnl_pct: Decimal | None
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class TimelineEventInput:
    event_type: str
    event_time: datetime | None
    source_message_id: int | None = None
    reply_to_message_id: int | None = None
    edit_time: datetime | None = None


@dataclass(frozen=True)
class TimelineReconciliationReport:
    is_consistent: bool
    ordered_event_types: tuple[str, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


class HistoricalOutcomeReconciliationService:
    """Separates source-reported outcomes from independently derived outcomes."""

    DEFAULT_TOLERANCE_PCT = Decimal("0.15")

    @staticmethod
    def _decimal(value: Any) -> Decimal | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            result = Decimal(str(value).replace(",", "").replace("%", "").strip())
        except (InvalidOperation, TypeError, ValueError):
            return None
        return result if result.is_finite() else None

    def check_reported_outcome(
        self,
        *,
        side: str | None,
        entry: Any,
        exit_price: Any,
        reported_pnl_pct: Any,
        tolerance_pct: Any = DEFAULT_TOLERANCE_PCT,
    ) -> ReportedOutcomeReport:
        normalized_side = str(side or "").upper()
        entry_value = self._decimal(entry)
        exit_value = self._decimal(exit_price)
        reported = self._decimal(reported_pnl_pct)
        tolerance = self._decimal(tolerance_pct) or self.DEFAULT_TOLERANCE_PCT
        errors: list[str] = []
        warnings: list[str] = []
        if normalized_side not in {"LONG", "SHORT"}:
            errors.append("SIDE_INVALID")
        if entry_value is None or entry_value <= 0:
            errors.append("ENTRY_INVALID")
        if exit_value is None or exit_value <= 0:
            errors.append("EXIT_PRICE_INVALID")
        if errors:
            return ReportedOutcomeReport("UNVERIFIABLE", None, reported, tuple(errors), tuple(warnings))
        assert entry_value is not None and exit_value is not None
        if normalized_side == "LONG":
            derived = ((exit_value - entry_value) / entry_value) * Decimal("100")
        else:
            derived = ((entry_value - exit_value) / entry_value) * Decimal("100")
        if reported is None:
            warnings.append("SOURCE_RESULT_MISSING")
            return ReportedOutcomeReport("UNVERIFIABLE", derived, None, tuple(errors), tuple(warnings))
        difference = abs(derived - reported)
        if difference <= tolerance:
            return ReportedOutcomeReport("MATCH", derived, reported, tuple(errors), tuple(warnings))
        warnings.extend(("SOURCE_RESULT_DIFFERS_FROM_PRICE_MOVE", "FINANCIAL_CONSISTENCY_REVIEW"))
        return ReportedOutcomeReport("MISMATCH", derived, reported, tuple(errors), tuple(warnings))

    @staticmethod
    def _utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return None
        from datetime import timezone

        return value.astimezone(timezone.utc)

    def reconcile_timeline(
        self,
        events: Iterable[TimelineEventInput],
    ) -> TimelineReconciliationReport:
        items = list(events)
        errors: list[str] = []
        warnings: list[str] = []
        for item in items:
            if item.event_time is None or self._utc(item.event_time) is None:
                warnings.append(f"EVENT_TIME_MISSING:{item.event_type}")
            if item.edit_time is not None and item.event_time is not None:
                edit_time = self._utc(item.edit_time)
                event_time = self._utc(item.event_time)
                if edit_time and event_time and edit_time > event_time:
                    warnings.append(f"EDIT_AFTER_EVENT:{item.event_type}")
        ordered = sorted(
            items,
            key=lambda item: self._utc(item.event_time) or datetime.max,
        )
        ordered_types = tuple(item.event_type for item in ordered)
        seen: set[tuple[str, int | None]] = set()
        activated = False
        closed = False
        for item in ordered:
            key = (item.event_type, item.source_message_id)
            if key in seen:
                errors.append(f"DUPLICATE_EVENT:{item.event_type}:{item.source_message_id}")
            seen.add(key)
            event_type = item.event_type.upper()
            if event_type == "ACTIVATED":
                activated = True
            elif event_type in {"SL", "CLOSE", "CLOSED", "FINAL_CLOSE"}:
                if not activated:
                    errors.append("CLOSE_BEFORE_ACTIVATION")
                closed = True
            elif event_type.startswith("TP") or event_type == "PARTIAL_EXIT":
                if not activated:
                    errors.append(f"{event_type}_BEFORE_ACTIVATION")
                if closed:
                    errors.append(f"{event_type}_AFTER_CLOSE")
        return TimelineReconciliationReport(
            is_consistent=not errors,
            ordered_event_types=ordered_types,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )
