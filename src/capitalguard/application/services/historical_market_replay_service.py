from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from capitalguard.domain.coverage import CoverageStatus, HistoricalCoverage, calculate_historical_coverage, interval_delta
from capitalguard.domain.simulation_clock import SimulationClock
from capitalguard.infrastructure.db.models import (
    HistoricalMarketEvidence,
    HistoricalReplayRun,
    HistoricalRecommendationDraft,
    HistoricalSignal,
    HistoricalSignalEvent,
    HistoricalSignalMaterialization,
)

from .historical_signal_service import HistoricalSignalService, HistoricalSignalValidationError


REPLAY_VERSION = "G6-R1"
REPLAY_POLICY_VERSION = "G6-OHLCV-UTC-1"


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
    def _artifact_payload(*, signal_id: int, asset: str | None, market: str | None, interval: str, candles: list[MarketCandle], replay_end: datetime, provider_endpoint: str | None, replay_version: str = REPLAY_VERSION) -> dict:
        return {
            "signal_id": signal_id,
            "asset": asset,
            "market": market,
            "interval": interval,
            "replay_end": replay_end.isoformat(),
            "provider_endpoint": provider_endpoint,
            "replay_version": replay_version,
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

    @staticmethod
    def _request_fingerprint(*, signal_id: int, materialization_id: int, start: datetime, replay_end: datetime, interval: str, limit: int) -> str:
        payload = {
            "signal_id": signal_id,
            "materialization_id": materialization_id,
            "start": start.isoformat(),
            "replay_end": replay_end.isoformat(),
            "interval": interval,
            "limit": limit,
            "replay_version": REPLAY_VERSION,
            "policy_version": REPLAY_POLICY_VERSION,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def _record_market_evidence(
        self,
        session: Session,
        *,
        signal_id: int,
        asset: str | None,
        market: str | None,
        interval: str,
        candles: list[MarketCandle],
        replay_end: datetime,
        provider_endpoint: str | None = None,
        replay_run_id: int | None = None,
        fetched_at: datetime | None = None,
        data_as_of_status: str = "UNVERIFIABLE",
        ambiguity_status: str = "NONE",
        quality_status: str = "UNASSESSED",
    ) -> HistoricalMarketEvidence | None:
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
        range_start = min(self._utc(candle.open_time) for candle in candles)
        range_end = max(self._utc(candle.open_time) for candle in candles)
        providers = sorted({candle.data_source for candle in candles if candle.data_source})
        provider = providers[0] if len(providers) == 1 else "MULTI_SOURCE"
        artifact_key = f"signal:{signal_id}:interval:{interval}:start:{range_start.isoformat()}:end:{range_end.isoformat()}:hash:{artifact_hash}"
        replay_run_ref = None
        if replay_run_id is not None:
            run = session.get(HistoricalReplayRun, replay_run_id)
            if run is None:
                raise HistoricalSignalValidationError("ReplayRun does not exist")
            replay_run_ref = run.run_ref
        existing = session.execute(select(HistoricalMarketEvidence).where(HistoricalMarketEvidence.artifact_key == artifact_key)).scalar_one_or_none()
        if existing is not None:
            if replay_run_id is not None and existing.replay_run_id is None:
                existing.replay_run_id = replay_run_id
            existing_metadata = dict(existing.metadata_json or {})
            existing_metadata.update({
                "replay_version": REPLAY_VERSION,
                "data_as_of_status": existing_metadata.get("data_as_of_status", data_as_of_status),
                "fetched_at": existing_metadata.get("fetched_at", fetched_at.isoformat() if fetched_at else None),
                "ambiguity_status": ambiguity_status,
                "quality_status": quality_status,
            })
            existing.metadata_json = existing_metadata
            session.flush()
            return existing
        evidence = HistoricalMarketEvidence(
            signal_id=signal_id,
            replay_run_id=replay_run_id,
            replay_run_ref=replay_run_ref or f"HMKT-{uuid4().hex[:24].upper()}",
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
            metadata_json={
                **payload,
                "replay_version": REPLAY_VERSION,
                "fetched_at": fetched_at.isoformat() if fetched_at else None,
                "data_as_of_status": data_as_of_status,
                "ambiguity_status": ambiguity_status,
                "quality_status": quality_status,
            },
        )
        try:
            with session.begin_nested():
                session.add(evidence)
                session.flush()
        except IntegrityError:
            existing = session.execute(select(HistoricalMarketEvidence).where(HistoricalMarketEvidence.artifact_key == artifact_key)).scalar_one_or_none()
            if existing is None:
                raise
            if replay_run_id is not None and existing.replay_run_id is None:
                existing.replay_run_id = replay_run_id
                session.flush()
            return existing
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
        signal = session.get(HistoricalSignal, signal_id)
        if signal is None:
            raise HistoricalSignalValidationError("Historical signal does not exist")
        entry = self._decimal(signal.entry)
        stop = self._decimal(signal.stop_loss)
        targets = signal.targets if isinstance(signal.targets, list) else []
        target_levels = [self._decimal(target.get("price")) for target in targets if isinstance(target, dict)]
        return signal, entry, stop, [level for level in target_levels if level is not None]

    def _g5_materialization(self, session: Session, *, signal_id: int, materialization_id: int) -> HistoricalSignalMaterialization:
        materialization = session.get(HistoricalSignalMaterialization, materialization_id)
        if materialization is None or materialization.signal_id != signal_id:
            raise HistoricalSignalValidationError("G6 requires a matching G5 materialization")
        return materialization

    @staticmethod
    def _source_lifecycle(session: Session, *, signal_id: int) -> list[dict]:
        rows = session.execute(
            select(HistoricalSignalMaterialization, HistoricalRecommendationDraft)
            .join(HistoricalRecommendationDraft, HistoricalRecommendationDraft.id == HistoricalSignalMaterialization.draft_id)
            .where(HistoricalSignalMaterialization.signal_id == signal_id)
            .order_by(HistoricalSignalMaterialization.source_timestamp, HistoricalSignalMaterialization.id)
        ).all()
        return [
            {
                "materialization_id": materialization.id,
                "draft_id": draft.id,
                "materialization_kind": materialization.materialization_kind,
                "draft_kind": draft.draft_kind,
                "revision_id": materialization.revision_id,
                "source_timestamp": materialization.source_timestamp.isoformat(),
                "related_materialization_id": materialization.related_materialization_id,
            }
            for materialization, draft in rows
        ]

    def _get_or_create_run(
        self,
        session: Session,
        *,
        signal_id: int,
        materialization_id: int,
        start: datetime,
        replay_end: datetime,
        interval: str,
        limit: int,
    ) -> tuple[HistoricalReplayRun, bool]:
        fingerprint = self._request_fingerprint(
            signal_id=signal_id,
            materialization_id=materialization_id,
            start=start,
            replay_end=replay_end,
            interval=interval,
            limit=limit,
        )
        existing = session.execute(select(HistoricalReplayRun).where(HistoricalReplayRun.request_fingerprint == fingerprint)).scalar_one_or_none()
        if existing is not None:
            return existing, False
        run = HistoricalReplayRun(
            run_ref=f"HREP-{uuid4().hex[:24].upper()}",
            signal_id=signal_id,
            materialization_id=materialization_id,
            request_fingerprint=fingerprint,
            replay_version=REPLAY_VERSION,
            policy_version=REPLAY_POLICY_VERSION,
            status="RUNNING",
            window_start=start,
            window_end=replay_end,
            interval=interval,
            limit_count=limit,
        )
        try:
            with session.begin_nested():
                session.add(run)
                session.flush()
        except IntegrityError:
            existing = session.execute(select(HistoricalReplayRun).where(HistoricalReplayRun.request_fingerprint == fingerprint)).scalar_one_or_none()
            if existing is None:
                raise
            return existing, False
        return run, True

    @staticmethod
    def _coverage_from_candles(*, start: datetime, end: datetime, interval: str, candles: list[MarketCandle]) -> HistoricalCoverage:
        return calculate_historical_coverage(
            requested_start=start,
            requested_end=end,
            candle_times=(candle.open_time for candle in candles),
            interval=interval_delta(interval),
        )

    def replay_g6(
        self,
        session: Session,
        *,
        signal_id: int,
        materialization_id: int,
        start: datetime,
        replay_end: datetime,
        interval: str = "1m",
        limit: int = 1500,
        provider=None,
    ) -> dict:
        """Run G6 from an existing G5 materialization; caller owns commit/rollback."""
        self._g5_materialization(session, signal_id=signal_id, materialization_id=materialization_id)
        signal, _, _, _ = self._signal_levels(session, signal_id)
        source_lifecycle = self._source_lifecycle(session, signal_id=signal_id)
        start_utc, end_utc = self._utc(start), self._utc(replay_end)
        if start_utc >= end_utc:
            raise HistoricalSignalValidationError("Replay window is invalid")
        run, created = self._get_or_create_run(
            session,
            signal_id=signal_id,
            materialization_id=materialization_id,
            start=start_utc,
            replay_end=end_utc,
            interval=interval,
            limit=limit,
        )
        if not created and run.status in {"COMPLETED", "COMPLETED_UNVERIFIABLE", "REPLAY_PARTIAL"}:
            events = session.execute(select(HistoricalSignalEvent).where(HistoricalSignalEvent.replay_run_id == run.id).order_by(HistoricalSignalEvent.event_timestamp, HistoricalSignalEvent.id)).scalars().all()
            return {"run": run, "events": events, "status": run.status, "replayed": True}
        if provider is None:
            from capitalguard.infrastructure.market.historical_ohlcv_provider import BinanceHistoricalOhlcvProvider
            provider = BinanceHistoricalOhlcvProvider()
        coverage: HistoricalCoverage
        try:
            if hasattr(provider, "fetch_with_coverage"):
                candles, endpoint, coverage = provider.fetch_with_coverage(
                    asset=str(signal.asset or ""),
                    market=signal.market,
                    interval=interval,
                    start=start_utc,
                    end=end_utc,
                    limit=limit,
                )
            else:
                candles, endpoint = provider.fetch(
                    asset=str(signal.asset or ""),
                    market=signal.market,
                    interval=interval,
                    start=start_utc,
                    end=end_utc,
                    limit=limit,
                )
                coverage = self._coverage_from_candles(start=start_utc, end=end_utc, interval=interval, candles=candles)
        except Exception as exc:
            from capitalguard.infrastructure.market.binance_client import HistoricalMarketProviderError
            if not isinstance(exc, HistoricalMarketProviderError):
                raise
            run.status = "FAILED"
            run.failure_reason = str(exc)[:500]
            run.failed_at = datetime.now(timezone.utc)
            run.quality_status = "UNVERIFIED"
            session.flush()
            return {"run": run, "events": [], "status": "FAILED", "failure_reason": str(exc)}
        if not candles:
            run.coverage_status = CoverageStatus.UNAVAILABLE.value
            run.coverage_ratio = 0.0
            run.status = "REPLAY_PARTIAL"
            run.failure_reason = "Historical candle provider returned no evidence"
            run.failed_at = datetime.now(timezone.utc)
            run.quality_status = "UNVERIFIED"
            session.flush()
            return {"run": run, "events": [], "status": run.status, "failure_reason": run.failure_reason}

        fetched_at = datetime.now(timezone.utc)
        run.coverage_status = coverage.status.value
        run.coverage_ratio = coverage.coverage_ratio
        run.actual_start = coverage.actual_start
        run.actual_end = coverage.actual_end
        run.provider = sorted({candle.data_source for candle in candles if candle.data_source})[0] if candles else None
        run.provider_endpoint = endpoint
        run.data_source = run.provider
        run.provider_metadata = {
            "provider": run.provider,
            "provider_version": "UNVERIFIED",
            "endpoint": endpoint,
            "interval": interval,
            "limit": limit,
            "data_as_of_status": "UNVERIFIABLE",
            "coverage_status": coverage.status.value,
            "coverage_ratio": coverage.coverage_ratio,
            "requested_start": start_utc.isoformat(),
            "requested_end": end_utc.isoformat(),
            "actual_start": coverage.actual_start.isoformat() if coverage.actual_start else None,
            "actual_end": coverage.actual_end.isoformat() if coverage.actual_end else None,
            "expected_candles": coverage.expected_candles,
            "actual_candles": coverage.actual_candles,
            "gaps": [[gap_start.isoformat(), gap_end.isoformat()] for gap_start, gap_end in coverage.gaps],
        }
        run.fetched_at = fetched_at
        run.data_as_of_status = "UNVERIFIABLE"
        events = self.replay_candles(
            session,
            signal_id=signal_id,
            candles=candles,
            replay_end=end_utc,
            interval=interval,
            provider_endpoint=endpoint,
            replay_run_id=run.id,
            fetched_at=fetched_at,
            data_as_of_status=run.data_as_of_status,
            refresh_ranking=False,
        )
        evidence = session.execute(select(HistoricalMarketEvidence).where(HistoricalMarketEvidence.replay_run_id == run.id)).scalars().first()
        run.dataset_hash = evidence.artifact_hash if evidence else self._artifact_hash(self._artifact_payload(
            signal_id=signal_id,
            asset=signal.asset,
            market=signal.market,
            interval=interval,
            candles=candles,
            replay_end=end_utc,
            provider_endpoint=endpoint,
        ))
        run.ambiguity_status = "AMBIGUOUS" if any(event.replay_status == "AMBIGUOUS" for event in events) else "NONE"
        run.quality_status = "UNVERIFIABLE" if run.ambiguity_status == "AMBIGUOUS" or run.data_as_of_status != "VERIFIED" else "UNASSESSED"
        if coverage.status in {CoverageStatus.PARTIAL_WINDOW, CoverageStatus.GAPPED}:
            run.status = "REPLAY_PARTIAL"
        else:
            run.status = "COMPLETED_UNVERIFIABLE" if run.ambiguity_status == "AMBIGUOUS" or run.data_as_of_status != "VERIFIED" else "COMPLETED"
        run.result_json = {
            "event_ids": [event.id for event in events],
            "event_count": len(events),
            "evidence_id": evidence.id if evidence else None,
            "ambiguity_status": run.ambiguity_status,
            "coverage": {
                "status": coverage.status.value,
                "ratio": coverage.coverage_ratio,
                "requested_start": start_utc.isoformat(),
                "requested_end": end_utc.isoformat(),
                "actual_start": coverage.actual_start.isoformat() if coverage.actual_start else None,
                "actual_end": coverage.actual_end.isoformat() if coverage.actual_end else None,
                "expected_candles": coverage.expected_candles,
                "actual_candles": coverage.actual_candles,
                "gaps": [[gap_start.isoformat(), gap_end.isoformat()] for gap_start, gap_end in coverage.gaps],
            },
            "source_lifecycle": source_lifecycle,
        }
        run.completed_at = datetime.now(timezone.utc)
        session.flush()
        return {"run": run, "events": events, "status": run.status, "replayed": not created, "coverage": coverage}

    def replay(
        self,
        session: Session,
        *,
        signal_id: int,
        observations: Iterable[MarketObservation],
        replay_end: datetime,
        replay_run_id: int | None = None,
        refresh_ranking: bool = True,
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
            normalized.append(MarketObservation(observation.asset.upper(), observation.market, timestamp, price, observation.data_source))
        normalized.sort(key=lambda item: item.as_of)
        events: list[HistoricalSignalEvent] = []
        activated = False
        closed = False
        hit_targets: set[int] = set()
        dedup_prefix = f"g6:{replay_run_id}" if replay_run_id is not None else f"replay:{signal.id}"
        clock = SimulationClock(self._utc(signal.decision_timestamp))
        for observation in normalized:
            clock.advance_to(observation.as_of)
            if observation.as_of < self._utc(signal.decision_timestamp) or closed:
                continue
            if not activated and entry is not None and self._hit(signal.side, observation.price, entry):
                events.append(self.signal_service.record_event(session, signal_id=signal.id, event_type="ACTIVATED", event_timestamp=clock.current_time, market_as_of=clock.current_time, data_source=observation.data_source, price=observation.price, replay_status="VERIFIED", event_confidence="1.0000", event_data={"replay_end": end_time.isoformat(), "replay_run_id": replay_run_id}, dedup_key=f"{dedup_prefix}:ACTIVATED", replay_run_id=replay_run_id, refresh_ranking=refresh_ranking))
                activated = True
            if not activated:
                continue
            if stop is not None and self._stop_hit(signal.side, observation.price, stop):
                events.append(self.signal_service.record_event(session, signal_id=signal.id, event_type="SL", event_timestamp=clock.current_time, market_as_of=clock.current_time, data_source=observation.data_source, price=observation.price, replay_status="VERIFIED", event_confidence="1.0000", event_data={"replay_end": end_time.isoformat(), "replay_run_id": replay_run_id}, dedup_key=f"{dedup_prefix}:SL", replay_run_id=replay_run_id, refresh_ranking=refresh_ranking))
                closed = True
                continue
            for index, level in enumerate(target_levels, start=1):
                if index in hit_targets or not self._hit(signal.side, observation.price, level):
                    continue
                events.append(self.signal_service.record_event(session, signal_id=signal.id, event_type=f"TP{index}", event_timestamp=clock.current_time, market_as_of=clock.current_time, data_source=observation.data_source, price=level, replay_status="VERIFIED", event_confidence="1.0000", event_data={"target_index": index, "replay_end": end_time.isoformat(), "replay_run_id": replay_run_id}, dedup_key=f"{dedup_prefix}:TP{index}", replay_run_id=replay_run_id, refresh_ranking=refresh_ranking))
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
        replay_run_id: int | None = None,
        fetched_at: datetime | None = None,
        data_as_of_status: str = "UNVERIFIABLE",
        refresh_ranking: bool = True,
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
        dedup_prefix = f"g6:{replay_run_id}" if replay_run_id is not None else f"replay:{signal.id}"
        decision_time = self._utc(signal.decision_timestamp)
        clock = SimulationClock(decision_time)
        eligible_candles = [candle for candle in normalized if self._utc(candle.open_time) >= decision_time]
        ambiguity_status = "NONE"
        market_evidence = self._record_market_evidence(
            session,
            signal_id=signal.id,
            asset=signal.asset,
            market=signal.market,
            interval=interval,
            candles=normalized,
            replay_end=end_time,
            provider_endpoint=provider_endpoint,
            replay_run_id=replay_run_id,
            fetched_at=fetched_at,
            data_as_of_status=data_as_of_status,
            ambiguity_status=ambiguity_status,
            quality_status="UNASSESSED",
        )
        for candle in eligible_candles:
            clock.advance_to(self._utc(candle.open_time))
            candle_time = clock.current_time
            if closed:
                continue
            activation_hit = entry is not None and (candle.high >= entry if str(signal.side or "").upper() == "LONG" else candle.low <= entry)
            if not activated and activation_hit:
                events.append(self.signal_service.record_event(session, signal_id=signal.id, event_type="ACTIVATED", event_timestamp=candle_time, market_as_of=candle_time, data_source=candle.data_source, price=entry, replay_status="VERIFIED", event_confidence="1.0000", event_data={"replay_end": end_time.isoformat(), "candle_rule": "OHLCV", "market_evidence_ref": market_evidence.replay_run_ref if market_evidence else None, "replay_run_id": replay_run_id}, dedup_key=f"{dedup_prefix}:ACTIVATED", replay_run_id=replay_run_id, refresh_ranking=refresh_ranking))
                activated = True
            if not activated:
                continue
            stop_hit = stop is not None and self._candle_stop_hit(signal.side, candle, stop)
            target_hits = [index for index, level in enumerate(target_levels, start=1) if index not in hit_targets and self._candle_target_hit(signal.side, candle, level)]
            if stop_hit and target_hits and replay_run_id is None:
                events.append(self.signal_service.record_event(session, signal_id=signal.id, event_type="SL", event_timestamp=candle_time, market_as_of=candle_time, data_source=candle.data_source, price=stop, replay_status="VERIFIED", event_confidence="1.0000", event_data={"replay_end": end_time.isoformat(), "candle_rule": "PESSIMISTIC_SL_FIRST", "market_evidence_ref": market_evidence.replay_run_ref if market_evidence else None, "replay_run_id": replay_run_id}, dedup_key=f"{dedup_prefix}:SL", replay_run_id=replay_run_id, refresh_ranking=refresh_ranking))
                closed = True
                continue
            if stop_hit and target_hits and replay_run_id is not None:
                ambiguity_status = "AMBIGUOUS"
                events.append(self.signal_service.record_event(session, signal_id=signal.id, event_type="AMBIGUOUS", event_timestamp=candle_time, market_as_of=candle_time, data_source=candle.data_source, price=None, replay_status="AMBIGUOUS", event_confidence="0.0000", event_data={"replay_end": end_time.isoformat(), "candle_rule": "PESSIMISTIC_SL_FIRST_INFERRED", "possible_events": ["SL", *[f"TP{index}" for index in target_hits]], "high": str(candle.high), "low": str(candle.low), "market_evidence_ref": market_evidence.replay_run_ref if market_evidence else None, "replay_run_id": replay_run_id}, dedup_key=f"{dedup_prefix}:AMBIGUOUS:{candle_time.isoformat()}", replay_run_id=replay_run_id, refresh_ranking=refresh_ranking))
                closed = True
                continue
            if stop_hit:
                events.append(self.signal_service.record_event(session, signal_id=signal.id, event_type="SL", event_timestamp=candle_time, market_as_of=candle_time, data_source=candle.data_source, price=stop, replay_status="VERIFIED", event_confidence="1.0000", event_data={"replay_end": end_time.isoformat(), "candle_rule": "OHLCV", "market_evidence_ref": market_evidence.replay_run_ref if market_evidence else None, "replay_run_id": replay_run_id}, dedup_key=f"{dedup_prefix}:SL", replay_run_id=replay_run_id, refresh_ranking=refresh_ranking))
                closed = True
                continue
            for index in target_hits:
                level = target_levels[index - 1]
                events.append(self.signal_service.record_event(session, signal_id=signal.id, event_type=f"TP{index}", event_timestamp=candle_time, market_as_of=candle_time, data_source=candle.data_source, price=level, replay_status="VERIFIED", event_confidence="1.0000", event_data={"target_index": index, "replay_end": end_time.isoformat(), "candle_rule": "OHLCV", "market_evidence_ref": market_evidence.replay_run_ref if market_evidence else None, "replay_run_id": replay_run_id}, dedup_key=f"{dedup_prefix}:TP{index}", replay_run_id=replay_run_id, refresh_ranking=refresh_ranking))
                hit_targets.add(index)
            if target_levels and len(hit_targets) == len(target_levels):
                closed = True
        if market_evidence is not None:
            metadata = dict(market_evidence.metadata_json or {})
            metadata["ambiguity_status"] = ambiguity_status
            metadata["quality_status"] = "UNVERIFIABLE" if ambiguity_status == "AMBIGUOUS" else "UNASSESSED"
            market_evidence.metadata_json = metadata
            session.flush()
        return events

    def replay_from_binance(
        self,
        session: Session,
        *,
        signal_id: int,
        start: datetime,
        replay_end: datetime,
        interval: str = "1m",
        limit: int = 1500,
        provider=None,
        replay_run_id: int | None = None,
        refresh_ranking: bool = True,
        fetched_at: datetime | None = None,
        data_as_of_status: str = "UNVERIFIABLE",
    ) -> list[HistoricalSignalEvent]:
        """Fetch bounded historical OHLCV; caller owns transaction disposition."""
        signal, _, _, _ = self._signal_levels(session, signal_id)
        if provider is None:
            from capitalguard.infrastructure.market.historical_ohlcv_provider import BinanceHistoricalOhlcvProvider
            provider = BinanceHistoricalOhlcvProvider()
        candles, endpoint = provider.fetch(
            asset=str(signal.asset or ""),
            market=signal.market,
            interval=interval,
            start=self._utc(start),
            end=self._utc(replay_end),
            limit=limit,
        )
        if not candles:
            raise HistoricalSignalValidationError("Historical candle provider returned no evidence")
        return self.replay_candles(
            session,
            signal_id=signal_id,
            candles=candles,
            replay_end=replay_end,
            interval=interval,
            provider_endpoint=endpoint,
            replay_run_id=replay_run_id,
            fetched_at=fetched_at,
            data_as_of_status=data_as_of_status,
            refresh_ranking=refresh_ranking,
        )
