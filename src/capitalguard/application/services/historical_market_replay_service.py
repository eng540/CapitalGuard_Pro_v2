"""Deterministic point-in-time replay for historical signals."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import HistoricalSignalEvent

from .historical_signal_service import HistoricalSignalService, HistoricalSignalValidationError


@dataclass(frozen=True)
class MarketObservation:
    asset: str
    market: str | None
    as_of: datetime
    price: Decimal
    data_source: str


class HistoricalMarketReplayService:
    def __init__(self, signal_service: HistoricalSignalService | None = None):
        self.signal_service = signal_service or HistoricalSignalService()

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise HistoricalSignalValidationError("Market observation timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _decimal(value) -> Decimal | None:
        if value is None:
            return None
        try:
            result = Decimal(str(value))
            return result if result.is_finite() else None
        except Exception:
            return None

    @staticmethod
    def _hit(side: str | None, price: Decimal, level: Decimal) -> bool:
        return price >= level if str(side or "").upper() == "LONG" else price <= level

    def replay(
        self,
        session: Session,
        *,
        signal_id: int,
        observations: Iterable[MarketObservation],
        replay_end: datetime,
    ) -> list[HistoricalSignalEvent]:
        from capitalguard.infrastructure.db.models import HistoricalSignal

        signal = session.get(HistoricalSignal, signal_id)
        if signal is None:
            raise HistoricalSignalValidationError("Historical signal does not exist")
        end_time = self._utc(replay_end)
        normalized: list[MarketObservation] = []
        for observation in observations:
            timestamp = self._utc(observation.as_of)
            price = self._decimal(observation.price)
            if price is None or price <= 0:
                raise HistoricalSignalValidationError("Market observation price must be positive")
            if timestamp > end_time:
                raise HistoricalSignalValidationError("Market observation is after replay_end")
            if observation.asset.upper() != str(signal.asset or "").upper():
                continue
            if signal.market and observation.market and observation.market.upper() != signal.market.upper():
                continue
            normalized.append(
                MarketObservation(
                    asset=observation.asset.upper(),
                    market=observation.market,
                    as_of=timestamp,
                    price=price,
                    data_source=observation.data_source,
                )
            )
        normalized.sort(key=lambda item: item.as_of)
        entry = self._decimal(signal.entry)
        stop = self._decimal(signal.stop_loss)
        targets = signal.targets if isinstance(signal.targets, list) else []
        target_levels = [self._decimal(target.get("price")) for target in targets if isinstance(target, dict)]
        target_levels = [level for level in target_levels if level is not None]
        events: list[HistoricalSignalEvent] = []
        activated = False
        closed = False
        hit_targets: set[int] = set()
        for observation in normalized:
            if observation.as_of < self._utc(signal.decision_timestamp):
                continue
            if closed:
                break
            if not activated and entry is not None and self._hit(signal.side, observation.price, entry):
                event = self.signal_service.record_event(
                    session,
                    signal_id=signal.id,
                    event_type="ACTIVATED",
                    event_timestamp=observation.as_of,
                    market_as_of=observation.as_of,
                    data_source=observation.data_source,
                    price=observation.price,
                    replay_status="VERIFIED",
                    event_confidence="1.0000",
                    event_data={"replay_end": end_time.isoformat()},
                    dedup_key=f"replay:{signal.id}:ACTIVATED",
                )
                events.append(event)
                activated = True
            if stop is not None and self._stop_hit(signal.side, observation.price, stop):
                event = self.signal_service.record_event(
                    session,
                    signal_id=signal.id,
                    event_type="SL",
                    event_timestamp=observation.as_of,
                    market_as_of=observation.as_of,
                    data_source=observation.data_source,
                    price=observation.price,
                    replay_status="VERIFIED",
                    event_confidence="1.0000",
                    event_data={"replay_end": end_time.isoformat()},
                    dedup_key=f"replay:{signal.id}:SL",
                )
                events.append(event)
                closed = True
                continue
            for index, level in enumerate(target_levels, start=1):
                if index in hit_targets or not self._hit(signal.side, observation.price, level):
                    continue
                event = self.signal_service.record_event(
                    session,
                    signal_id=signal.id,
                    event_type=f"TP{index}",
                    event_timestamp=observation.as_of,
                    market_as_of=observation.as_of,
                    data_source=observation.data_source,
                    price=observation.price,
                    replay_status="VERIFIED",
                    event_confidence="1.0000",
                    event_data={"target_index": index, "replay_end": end_time.isoformat()},
                    dedup_key=f"replay:{signal.id}:TP{index}",
                )
                events.append(event)
                hit_targets.add(index)
            if target_levels and len(hit_targets) == len(target_levels):
                closed = True
        return events

    @staticmethod
    def _stop_hit(side: str | None, price: Decimal, stop: Decimal) -> bool:
        return price <= stop if str(side or "").upper() == "LONG" else price >= stop
