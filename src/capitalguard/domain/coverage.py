from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class CoverageStatus(str, Enum):
    FULL = "FULL"
    PARTIAL_WINDOW = "PARTIAL_WINDOW"
    GAPPED = "GAPPED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class HistoricalCoverage:
    requested_start: datetime
    requested_end: datetime
    actual_start: datetime | None
    actual_end: datetime | None
    expected_candles: int
    actual_candles: int
    coverage_ratio: float
    status: CoverageStatus
    gaps: tuple[tuple[datetime, datetime], ...] = ()

    @property
    def is_complete(self) -> bool:
        return self.status is CoverageStatus.FULL and self.coverage_ratio >= 0.999

    @property
    def missing_candles(self) -> int:
        return max(0, self.expected_candles - self.actual_candles)
