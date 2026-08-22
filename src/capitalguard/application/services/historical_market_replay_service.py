from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import HistoricalMarketEvidence, HistoricalSignalEvent

from .historical_signal_service import HistoricalSignalService, HistoricalSignalValidationError


@dataclass(frozen=True)
class MarketObservation:
    asset: str
    market: str | None
    as_of: datetime
    price: Decimal
    data_source: str


@dataclass(frozen=True)
class MarketCandle:
    asset: str
    market: str | None
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    data_source: str


class CandleCache:
    """Deterministic in-memory cache for bounded historical fixture/provider candles."""

    def __init__(self) -> None:
        self._candles: dict[tuple[str, str | None, datetime], MarketCandle] = {}

    def put_many(self, candles: Iterable[MarketCandle]) -> None:
        for candle in candles:
            key = (candle.asset.upper(), candle.market.upper() if candle.market else None, candle.open_time)
            self._candles[key] = candle

    def get(self, *, asset: str, market: str | None, start: datetime, end: datetime) -> list[MarketCandle]:
        normalized_asset = asset.upper()
        normalized_market = market.upper() if market else None
        return sorted(
            [
                candle
                for (item_asset, item_market, timestamp), candle in self._candles.items()
                if item_asset == normalized_asset
                and (normalized_market is None or item_market == normalized_market)
                and start <= timestamp <= end
            ],
            key=lambda item: item.open_time,
        )


class HistoricalMarketReplayService:
    def __init__(
        self,
        signal_service: HistoricalSignalService | None = None,
        candle_cache: CandleCache | None = None,
    ):
        self.signal_service = signal_service or HistoricalSignalService()
        self.candle_cache = candle_cache or CandleCache()

    @staticmethod
    def _artifact_payload(*, signal_id: int, asset: str | None, market: str | None, interval: str, candles: list[MarketCandle], replay_end: datetime, provider_endpoint: str | None) -> dict:
        return {
            "signal_id": signal_id,
            "asset": asset,
            "market": market,
            "interval": interval,
            "replay_end": replay_end.isoformat(),
            "provider_endpoint": provider_endpoint,
            "candles": [
                {
                    "open_time": candle.open_time.isoformat(),
                    "open": str(candle.open),
                    "high": str(candle.high),
                    "low": str(candle.low),
                    "close": str(candle.close),
                    "volume": str(candle.volume),
                    "data_source": candle.data_source,
                }
                for candle in candles
            ],
        }

    @staticmethod
    def _artifact_hash(payload: dict) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _record_market_evidence(self, session: Session, *, signal_id: int, asset: str | None, market: str | None, interval: str, candles: list[MarketCandle], replay_end: datetime, provider_endpoint: str | None = None) -> HistoricalMarketEvidence | None:
        if not candles:
            return None
        payload = self._artifact_payload(
            signal_id=signal_id,
            asset=asset,
            market=market,
            interval=interval,
            candles=candles,
            replay_end=replay_end,
            provider_endpoint=provider_endpoint,
        )
        artifact_hash = self._artifact_hash(payload)
        range_start = min(candle.open_time for candle in candles)
        range_end = max(candle.open_time for candle in candles)
        providers = sorted({candle.data_source for candle in candles if candle.data_source})
        provider = providers[0] if len(providers) == 1 else "MULTI_SOURCE"
        artifact_key = f"signal:{signal_id}:interval:{interval}:start:{range_start.isoformat()}:end:{range_end.isoformat()}:hash:{artifact_hash}"
        existing = session.execute(select(HistoricalMarketEvidence).where(HistoricalMarketEvidence.artifact_key == artifact_key)).scalar_one_or_none()
        if existing is not None:
            return existing
        evidence = HistoricalMarketEvidence(
            signal_id=signal_id,
            replay_run_ref=f"HMKT-{uuid4().hex[:24].upper()}",
            provider=provider,
            provider_endpoint=provider_endpoint,
            asset=str(asset or "").upper(),
            market=market,
            interval=interval,
            range_start=range_start,
            range_end=range_end,
            candle_count=len(candles),
            artifact_hash=artifact_hash,
            artifact_key=artifact_key,
            metadata_json=payload,
        )
        session.add(evidence)
        session.flush()
        return evidence

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise HistoricalSignalValidationError("Market timestamps must be timezone-aware")
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

    @staticmethod
    def _stop_hit(side: str | None, price: Decimal, stop: Decimal) -> bool:
        return price <= stop if str(side or "").upper() == "LONG" else price >= stop

    @staticmethod
    def _candle_target_hit(side: str | None, candle: MarketCandle, level: Decimal) -> bool:
        return candle.high >= level if str(side or "").upper() == "LONG" else candle.low <= level

    @staticmethod
    def _candle_stop_hit(side: str | None, candle: MarketCandle, stop: Decimal) -> bool:
        return candle.low <= stop if str(side or "").upper() == "LONG" else candle.high >= stop

    def _signal_levels(self, session: Session, signal_id: int):
        from capitalguard.infrastructure.db.models import HistoricalSignal

        signal = session.get(HistoricalSignal, signal_id)
        if signal is None:
            raise HistoricalSignalValidationError("Historical signal does not exist")
        entry = self._decimal(signal.entry)
        stop = self._decimal(signal.stop_loss)
        targets = signal.targets if isinstance(signal.targets, list) else []
        target_levels = [self._decimal(target.get("price")) for target in targets if isinstance(target, dict)]
        return signal, entry, stop, [level for level in target_levels if level is not None]

    def replay(
        self,
        session: Session,
        *,
        signal_id: int,
        observations: Iterable[MarketObservation],
        replay_end: datetime,
    ) -> list[HistoricalSignalEvent]:
        signal, entry, stop, target_levels = self._signal_levels(session, signal_id)
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
                events.append(self.signal_service.record_event(
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
                ))
                activated = True
            if not activated:
                continue
            if stop is not None and self._stop_hit(signal.side, observation.price, stop):
                events.append(self.signal_service.record_event(
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
                ))
                closed = True
                continue
            for index, level in enumerate(target_levels, start=1):
                if index in hit_targets or not self._hit(signal.side, observation.price, level):
                    continue
                events.append(self.signal_service.record_event(
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
                ))
                hit_targets.add(index)
            if target_levels and len(hit_targets) == len(target_levels):
                closed = True
        return events

    def replay_candles(
        self,
        session: Session,
        *,
        signal_id: int,
        candles: Iterable[MarketCandle],
        replay_end: datetime,
        interval: str = "1m",
        provider_endpoint: str | None = None,
    ) -> list[HistoricalSignalEvent]:
        signal, entry, stop, target_levels = self._signal_levels(session, signal_id)
        end_time = self._utc(replay_end)
        normalized: list[MarketCandle] = []
        for candle in candles:
            timestamp = self._utc(candle.open_time)
            if timestamp > end_time:
                raise HistoricalSignalValidationError("Market candle is after replay_end")
            if candle.asset.upper() != str(signal.asset or "").upper():
                continue
            if signal.market and candle.market and candle.market.upper() != signal.market.upper():
                continue
            values = [self._decimal(value) for value in (candle.open, candle.high, candle.low, candle.close, candle.volume)]
            if any(value is None or value <= 0 for value in values):
                raise HistoricalSignalValidationError("OHLCV candle values must be positive")
            normalized.append(candle)
        normalized.sort(key=lambda item: self._utc(item.open_time))
        events: list[HistoricalSignalEvent] = []
        activated = False
        closed = False
        hit_targets: set[int] = set()
        decision_time = self._utc(signal.decision_timestamp)
        market_evidence = self._record_market_evidence(
            session,
            signal_id=signal.id,
            asset=signal.asset,
            market=signal.market,
            interval=interval,
            candles=normalized,
            replay_end=end_time,
            provider_endpoint=provider_endpoint,
        )
        for candle in normalized:
            candle_time = self._utc(candle.open_time)
            if candle_time < decision_time or closed:
                continue
            activation_hit = entry is not None and (
                candle.high >= entry if str(signal.side or "").upper() == "LONG" else candle.low <= entry
            )
            if not activated and activation_hit:
                events.append(self.signal_service.record_event(
                    session,
                    signal_id=signal.id,
                    event_type="ACTIVATED",
                    event_timestamp=candle_time,
                    market_as_of=candle_time,
                    data_source=candle.data_source,
                    price=entry,
                    replay_status="VERIFIED",
                    event_confidence="1.0000",
                    event_data={"replay_end": end_time.isoformat(), "candle_rule": "OHLCV", "market_evidence_ref": market_evidence.replay_run_ref if market_evidence else None},
                    dedup_key=f"replay:{signal.id}:ACTIVATED",
                ))
                activated = True
            if not activated:
                continue
            # When a candle crosses both stop and target, stop wins conservatively.
            if stop is not None and self._candle_stop_hit(signal.side, candle, stop):
                events.append(self.signal_service.record_event(
                    session,
                    signal_id=signal.id,
                    event_type="SL",
                    event_timestamp=candle_time,
                    market_as_of=candle_time,
                    data_source=candle.data_source,
                    price=stop,
                    replay_status="VERIFIED",
                    event_confidence="1.0000",
                    event_data={
                        "replay_end": end_time.isoformat(),
                        "candle_rule": "PESSIMISTIC_SL_FIRST",
                        "market_evidence_ref": market_evidence.replay_run_ref if market_evidence else None,
                        "high": str(candle.high),
                        "low": str(candle.low),
                    },
                    dedup_key=f"replay:{signal.id}:SL",
                ))
                closed = True
                continue
            for index, level in enumerate(target_levels, start=1):
                if index in hit_targets or not self._candle_target_hit(signal.side, candle, level):
                    continue
                events.append(self.signal_service.record_event(
                    session,
                    signal_id=signal.id,
                    event_type=f"TP{index}",
                    event_timestamp=candle_time,
                    market_as_of=candle_time,
                    data_source=candle.data_source,
                    price=level,
                    replay_status="VERIFIED",
                    event_confidence="1.0000",
                    event_data={
                        "target_index": index,
                        "replay_end": end_time.isoformat(),
                        "candle_rule": "PESSIMISTIC_SL_FIRST",
                        "market_evidence_ref": market_evidence.replay_run_ref if market_evidence else None,
                    },
                    dedup_key=f"replay:{signal.id}:TP{index}",
                ))
                hit_targets.add(index)
            if target_levels and len(hit_targets) == len(target_levels):
                closed = True
        return events
