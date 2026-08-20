from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from capitalguard.domain.temporal import PriceValidity, decimal, utc


class PriceValidityService:
    """Deterministic live-eligibility check; historical decisions use replay instead."""

    DEFAULT_MAX_AGE_SECONDS = 180
    MIN_DRIFT_FLOOR = Decimal("0.0025")
    DRIFT_RISK_MULTIPLIER = Decimal("0.50")

    @staticmethod
    def _clamp(value: Decimal) -> Decimal:
        return max(Decimal("0"), min(Decimal("1"), value))

    def evaluate(
        self,
        *,
        source_time: datetime | None,
        received_time: datetime,
        current_price: Any = None,
        reference_price: Any = None,
        stop_loss: Any = None,
        max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
        market_data_available: bool = True,
        max_drift_pct: Any = None,
    ) -> PriceValidity:
        reasons: list[str] = []
        current = decimal(current_price)
        reference = decimal(reference_price)
        stop = decimal(stop_loss)
        received = utc(received_time)
        source = utc(source_time)
        if received is None:
            raise ValueError("received_time is required")

        if source is None:
            reasons.append("SOURCE_TIME_MISSING")
            freshness = Decimal("0")
        else:
            age = max(0, int((received - source).total_seconds()))
            if age <= max_age_seconds:
                freshness = Decimal("1") - (Decimal(age) / Decimal(max_age_seconds + 1))
                reasons.append("SOURCE_TIME_FRESH")
            else:
                freshness = Decimal("0")
                reasons.append("SOURCE_TOO_OLD")

        quality = Decimal("1") if market_data_available and current is not None else Decimal("0")
        if quality == 0:
            reasons.append("MARKET_SNAPSHOT_MISSING")

        if reference is None or reference <= 0 or current is None or current <= 0:
            distance = Decimal("0")
            reasons.append("PRICE_REFERENCE_INCOMPLETE")
            drift = None
            allowed_drift = None
        else:
            drift = abs(current - reference) / reference
            if max_drift_pct is not None:
                allowed_drift = decimal(max_drift_pct)
            elif stop is not None and stop > 0:
                risk = abs(reference - stop) / reference
                allowed_drift = max(self.MIN_DRIFT_FLOOR, risk * self.DRIFT_RISK_MULTIPLIER)
            else:
                allowed_drift = self.MIN_DRIFT_FLOOR
            distance = self._clamp(Decimal("1") - (drift / allowed_drift)) if allowed_drift > 0 else Decimal("0")
            if drift <= allowed_drift:
                reasons.append("PRICE_WITHIN_ENVELOPE")
            else:
                reasons.append("PRICE_OUTSIDE_ENVELOPE")

        score = (freshness * Decimal("0.40")) + (distance * Decimal("0.40")) + (quality * Decimal("0.20"))
        valid = bool(
            source is not None
            and source <= received
            and freshness > 0
            and distance > 0
            and quality > 0
            and current is not None
            and reference is not None
        )
        return PriceValidity(
            score=self._clamp(score),
            freshness_score=self._clamp(freshness),
            distance_score=self._clamp(distance),
            market_data_quality_score=quality,
            valid_for_live=valid,
            reason_codes=tuple(reasons),
            current_price=current,
            reference_price=reference,
            drift_pct=drift,
            max_drift_pct=allowed_drift,
        )
