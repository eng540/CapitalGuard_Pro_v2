# --- START OF FILE src/capitalguard/application/services/historical_market_replay_service.py ---

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from capitalguard.domain.coverage import CoverageStatus, HistoricalCoverage, calculate_historical_coverage, interval_delta
from capitalguard.domain.simulation_clock import SimulationClock
from capitalguard.infrastructure.market.intra_candle_resolver import IntraCandleResolver
from capitalguard.infrastructure.db.models import (
    HistoricalMarketEvidence,
    HistoricalReplayRun,
    HistoricalRecommendationDraft,
    HistoricalSignal,
    HistoricalSignalEvent,
    HistoricalSignalMaterialization,
    HistoricalForwardReceipt,
    HistoricalSignalEvidence,
)

from .adaptive_historical_replay import AdaptiveHistoricalReplayPlanner, LifecycleState
from .historical_replay_decision_service import HistoricalReplayDecisionAuthority
from .historical_replay_version import REPLAY_POLICY_VERSION, REPLAY_VERSION
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
        self.intra_candle_resolver = None

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
    def _request_fingerprint(*, signal_id: int, materialization_id: int, start: datetime, replay_end: datetime, interval: str, limit: int, retry_of_fingerprint: str | None = None) -> str:
        payload = {
            "signal_id": signal_id,
            "materialization_id": materialization_id,
            "start": start.isoformat(),
            "replay_end": replay_end.isoformat(),
            "interval": interval,
            "limit": limit,
            "retry_of_fingerprint": retry_of_fingerprint,
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
            replay_run_ref = f"HMKT-{uuid4().hex[:24].upper()}"
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
        target_levels = []
        seen_prices = set()
        for target in targets:
            if not isinstance(target, dict): continue
            level = self._decimal(target.get("price"))
            if level is None or level in seen_prices: continue
            seen_prices.add(level); target_levels.append(level)
        return signal, entry, stop, target_levels

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
        retry_of_fingerprint: str | None = None,
        reprocess_of_run_id: int | None = None,
    ) -> tuple[HistoricalReplayRun, bool]:
        fingerprint = self._request_fingerprint(
            signal_id=signal_id,
            materialization_id=materialization_id,
            start=start,
            replay_end=replay_end,
            interval=interval,
            limit=limit,
            retry_of_fingerprint=retry_of_fingerprint,
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
            reprocess_of_run_id=reprocess_of_run_id,
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

    def retry_g6(self, session: Session, *, receipt_id: int, provider=None) -> dict:
        """Re-evaluate a persisted replay through the single Decision Authority.

        This method deliberately has no status-only retry gate. A duplicate receipt
        may therefore resolve to REUSE, or create a new non-destructive ReplayRun.
        """
        import logging

        receipt = session.get(HistoricalForwardReceipt, receipt_id)
        if receipt is None or receipt.evidence_id is None:
            raise HistoricalSignalValidationError("Historical receipt does not have replayable evidence")
        evidence = session.get(HistoricalSignalEvidence, receipt.evidence_id)
        if evidence is None or not evidence.signals:
            raise HistoricalSignalValidationError("Historical receipt does not have a materialized signal")
        signal = sorted(evidence.signals, key=lambda item: item.id)[0]
        materialization = session.execute(
            select(HistoricalSignalMaterialization)
            .where(HistoricalSignalMaterialization.signal_id == signal.id)
            .order_by(HistoricalSignalMaterialization.id.desc())
        ).scalars().first()
        if materialization is None:
            raise HistoricalSignalValidationError("Historical signal has no G5 materialization")
        previous = session.execute(
            select(HistoricalReplayRun)
            .where(
                HistoricalReplayRun.signal_id == signal.id,
                HistoricalReplayRun.materialization_id == materialization.id,
            )
            .order_by(HistoricalReplayRun.started_at.desc(), HistoricalReplayRun.id.desc())
        ).scalars().first()

        authority = HistoricalReplayDecisionAuthority()
        decision = authority.decide(previous)

        # ✅ CONSCIOUS IMPLEMENTATION (D1): the audit record is emitted through
        # the ReplayDecision contract itself — no dict duplication, single source of truth.
        # The contract guarantees unavailable values are NULL, not fabricated.
        def emit_decision_audit(run=None) -> dict:
            audit = decision.audit_record(
                batch_id=getattr(receipt, "batch_id", None),
                receipt_id=receipt.id,
                signal_id=signal.id,
                materialization_id=materialization.id,
                new_run_id=getattr(run, "id", None),
                new_fingerprint=(
                    getattr(run, "request_fingerprint", None)
                    if decision.is_reprocess
                    else (getattr(previous, "request_fingerprint", None) if previous is not None else None)
                ),
                lineage_parent=getattr(previous, "id", None),
            )
            logging.getLogger(__name__).info(
                "G6_REPLAY_AUDIT %s",
                json.dumps(audit, sort_keys=True, default=str),
            )
            return audit

        logging.getLogger(__name__).info(
            "G6 replay decision action=%s reason=%s previous_run=%s previous_status=%s previous_version=%s current_version=%s previous_policy=%s current_policy=%s coverage=%s",
            decision.action.value, decision.reason.value, decision.previous_run_id,
            decision.previous_status, decision.previous_replay_version,
            decision.current_replay_version, decision.previous_policy_version,
            decision.current_policy_version, decision.coverage.reason,
        )

        if not decision.is_reprocess and previous is not None:
            audit = emit_decision_audit(previous)
            return {
                "run": previous,
                "events": list(previous.events or []),
                "status": previous.status,
                "replayed": True,
                "retry_skipped": True,
                "decision": decision,
                "audit_record": audit,
            }

        if previous is None:
            start = self._utc(signal.decision_timestamp)
            replay_end = datetime.now(timezone.utc)
            retry_of_fingerprint = None
            reprocess_of_run_id = None
            interval = "1m"
            limit = 1500
        else:
            start = previous.window_start
            replay_end = previous.window_end
            retry_of_fingerprint = previous.request_fingerprint
            reprocess_of_run_id = previous.id
            interval = previous.interval
            limit = previous.limit_count

        result = self.replay_g6(
            session,
            signal_id=signal.id,
            materialization_id=materialization.id,
            start=start,
            replay_end=replay_end,
            interval=interval,
            limit=limit,
            provider=provider,
            retry_of_fingerprint=retry_of_fingerprint,
            reprocess_of_run_id=reprocess_of_run_id,
        )
        result["decision"] = decision
        result["audit_record"] = emit_decision_audit(result.get("run"))
        return result

    def _replay_g6_annual_adaptive(
        self,
        session: Session,
        *,
        signal_id: int,
        materialization_id: int,
        start: datetime,
        replay_end: datetime,
        limit: int,
        provider,
        retry_of_fingerprint: str | None = None,
        reprocess_of_run_id: int | None = None,
    ) -> dict:
        """Traverse daily candles in 365-day chunks and drill down only critical days."""
        signal, entry, stop, target_levels = self._signal_levels(session, signal_id)
        source_lifecycle = self._source_lifecycle(session, signal_id=signal_id)
        end_utc = self._utc(replay_end)
        planner = AdaptiveHistoricalReplayPlanner()
        source_time = self._utc(signal.decision_timestamp)
        day_zero = planner.day_start(source_time)
        macro_start = day_zero + timedelta(days=1)
        run, created = self._get_or_create_run(
            session, signal_id=signal_id, materialization_id=materialization_id,
            start=source_time, replay_end=end_utc, interval="1m", limit=limit,
            retry_of_fingerprint=retry_of_fingerprint, reprocess_of_run_id=reprocess_of_run_id,
        )
        if not created and run.status in {"COMPLETED", "COMPLETED_UNVERIFIABLE"}:
            events = session.execute(select(HistoricalSignalEvent).where(HistoricalSignalEvent.replay_run_id == run.id).order_by(HistoricalSignalEvent.event_timestamp, HistoricalSignalEvent.id)).scalars().all()
            return {"run": run, "events": events, "status": run.status, "replayed": True}

        state = LifecycleState(
            activated=False,
            hit_target_indices=frozenset(),
            remaining_target_indices=frozenset(range(1, len(target_levels) + 1)),
            current_stop=stop,
            lifecycle_state="PENDING_ORDER",
            last_processed_timestamp=None,
        )
        all_events: list[HistoricalSignalEvent] = []
        drilldown_days: list[str] = []
        macro_chunks: list[dict] = []
        macro_statuses: list[str] = []
        minute_statuses: list[str] = []
        macro_times = []
        first_actual = None
        last_actual = None
        macro_expected = 0
        macro_actual = 0
        incomplete_after_activation = False
        fetched_at = datetime.now(timezone.utc)

        def range_touches(candle, level: Decimal, *, is_stop: bool = False) -> bool:
            side = str(signal.side or "").upper()
            if side == "LONG":
                return candle.low <= level if is_stop else candle.high >= level
            return candle.high >= level if is_stop else candle.low <= level

        # Day-0 is resolved causally from the exact signal source timestamp.
        # Daily macro traversal begins on the following UTC day.
        day_zero_window = planner.minute_window_for_day(
            day=day_zero, signal_source_time=source_time, now=end_utc
        )
        if day_zero_window is not None:
            minute_fetch = getattr(provider, "fetch_minute_day", None)
            if minute_fetch is None:
                raise HistoricalSignalValidationError("Adaptive G6 provider lacks critical-day minute adapter")
            minutes, minute_endpoint, minute_coverage = minute_fetch(
                asset=str(signal.asset or ""), market=signal.market,
                start=day_zero_window.start, end=day_zero_window.end,
            )
            minute_statuses.append(minute_coverage.status.value)
            if minutes:
                drilldown_days.append(day_zero.isoformat())
                day_events = self.replay_candles(
                    session, signal_id=signal_id, candles=minutes, replay_end=day_zero_window.end,
                    interval="1m", provider_endpoint=minute_endpoint, replay_run_id=run.id,
                    fetched_at=fetched_at, data_as_of_status="UNVERIFIABLE", refresh_ranking=False,
                    resolver_client=getattr(provider, "client", None), initial_state=state,
                )
                all_events.extend(day_events)
                hit = set(state.hit_target_indices)
                activated = state.activated
                lifecycle = state.lifecycle_state
                last_ts = state.last_processed_timestamp
                for event in day_events:
                    event_type = str(event.event_type)
                    last_ts = event.event_timestamp
                    if event_type == "ACTIVATED":
                        activated = True; lifecycle = "ACTIVE_POSITION"
                    elif event_type.startswith("TP") and event_type[2:].isdigit():
                        hit.add(int(event_type[2:])); lifecycle = "CLOSED_TARGETS" if len(hit) == len(target_levels) else "ACTIVE_POSITION"
                    elif event_type == "SL":
                        lifecycle = "CLOSED_STOP"
                    elif event_type == "CLOSE":
                        lifecycle = "CLOSED_UNVERIFIABLE"
                    elif event_type == "AMBIGUOUS":
                        lifecycle = "CLOSED_UNVERIFIABLE"
                state = LifecycleState(
                    activated=activated, hit_target_indices=frozenset(hit),
                    remaining_target_indices=frozenset(set(range(1, len(target_levels) + 1)) - hit),
                    current_stop=state.current_stop, lifecycle_state=lifecycle,
                    last_processed_timestamp=last_ts,
                )

        for annual in planner.annual_windows(macro_start, end_utc):
            daily_fetch = getattr(provider, "fetch_daily", None)
            if daily_fetch is None:
                raise HistoricalSignalValidationError("Adaptive G6 provider lacks daily coverage adapter")
            daily, endpoint, coverage = daily_fetch(
                asset=str(signal.asset or ""), market=signal.market,
                start=annual.start, end=annual.end, limit=annual.limit,
            )
            macro_statuses.append(coverage.status.value)
            macro_times.extend(self._utc(c.open_time) for c in daily)
            macro_expected += coverage.expected_candles
            macro_actual += coverage.actual_candles
            if coverage.actual_start is not None and (first_actual is None or coverage.actual_start < first_actual):
                first_actual = coverage.actual_start
            if coverage.actual_end is not None and (last_actual is None or coverage.actual_end > last_actual):
                last_actual = coverage.actual_end
            macro_chunks.append({
                "start": annual.start.isoformat(), "end": annual.end.isoformat(),
                "limit": annual.limit, "coverage_status": coverage.status.value,
                "coverage_ratio": coverage.coverage_ratio,
                "actual_start": coverage.actual_start.isoformat() if coverage.actual_start else None,
                "actual_end": coverage.actual_end.isoformat() if coverage.actual_end else None,
            })

            for day_candle in sorted(daily, key=lambda item: self._utc(item.open_time)):
                day = planner.day_start(day_candle.open_time)
                if day < macro_start or day >= end_utc:
                    continue
                is_day_zero = day == macro_start
                if is_day_zero:
                    critical = True
                elif state.activated:
                    critical = bool(
                        state.current_stop is not None and range_touches(day_candle, state.current_stop, is_stop=True)
                    ) or any(
                        index in state.remaining_target_indices and range_touches(day_candle, level)
                        for index, level in enumerate(target_levels, start=1)
                    )
                else:
                    critical = entry is not None and range_touches(day_candle, entry)
                if not critical:
                    continue

                window = planner.minute_window_for_day(
                    day=day, signal_source_time=self._utc(signal.decision_timestamp), now=end_utc
                )
                if window is None:
                    continue
                drilldown_days.append(day.isoformat())
                minute_fetch = getattr(provider, "fetch_minute_day", None)
                if minute_fetch is None:
                    raise HistoricalSignalValidationError("Adaptive G6 provider lacks critical-day minute adapter")
                minutes, minute_endpoint, minute_coverage = minute_fetch(
                    asset=str(signal.asset or ""), market=signal.market,
                    start=window.start, end=window.end,
                )
                minute_statuses.append(minute_coverage.status.value)
                if state.activated and minute_coverage.status.value in {"PARTIAL_WINDOW", "GAPPED", "UNAVAILABLE"}: incomplete_after_activation=True
                if not minutes:
                    event_time = self._utc(day_candle.open_time)
                    entry_touched = entry is not None and range_touches(day_candle, entry)
                    stop_level = state.current_stop
                    stop_touched = stop_level is not None and range_touches(day_candle, stop_level, is_stop=True)
                    target_events = [(i, level) for i, level in enumerate(target_levels, 1) if i in state.remaining_target_indices and range_touches(day_candle, level)]
                    if not state.activated:
                        if not entry_touched:
                            continue
                        possible = ['ACTIVATED']
                        ambiguous = bool(stop_touched or target_events)
                    else:
                        possible = (['SL'] if stop_touched else []) + [f'TP{i}' for i, _ in target_events]
                        if not possible:
                            continue
                        ambiguous = len(possible) != 1
                    if ambiguous:
                        all_events.append(self.signal_service.record_event(session, signal_id=signal_id, event_type='AMBIGUOUS', event_timestamp=event_time, market_as_of=event_time, data_source=day_candle.data_source, price=None, replay_status='AMBIGUOUS', event_confidence='0.0000', event_data={'replay_end': end_utc.isoformat(), 'candle_rule': 'DAILY_OHLC_NO_MINUTE_EVIDENCE', 'possible_events': possible, 'entry_touched': entry_touched, 'stop_touched': stop_touched, 'target_events': [f'TP{i}' for i, _ in target_events], 'high': str(day_candle.high), 'low': str(day_candle.low), 'replay_run_id': run.id}, dedup_key=f'g6:{run.id}:AMBIGUOUS:{event_time.isoformat()}', replay_run_id=run.id, refresh_ranking=False))
                        state = AdaptiveHistoricalReplayPlanner.lifecycle_after_event(state, event_type='AMBIGUOUS', timestamp=event_time)
                        break
                    event_type=possible[0]; target_index=int(event_type[2:]) if event_type.startswith('TP') else None
                    event_price=entry if event_type=='ACTIVATED' else stop_level if event_type=='SL' else target_levels[target_index-1]
                    all_events.append(self.signal_service.record_event(session, signal_id=signal_id, event_type=event_type, event_timestamp=event_time, market_as_of=event_time, data_source=day_candle.data_source, price=event_price, replay_status='INFERRED', event_confidence='0.5000', event_data={'replay_end': end_utc.isoformat(), 'candle_rule': 'DAILY_OHLC_SINGLE_EVENT', 'replay_run_id': run.id}, dedup_key=f'g6:{run.id}:{event_type}:{event_time.isoformat()}', replay_run_id=run.id, refresh_ranking=False))
                    state = AdaptiveHistoricalReplayPlanner.lifecycle_after_event(state, event_type=event_type, target_index=target_index, stop=state.current_stop, timestamp=event_time)
                    continue
                day_events = self.replay_candles(
                    session, signal_id=signal_id, candles=minutes, replay_end=window.end,
                    interval="1m", provider_endpoint=minute_endpoint, replay_run_id=run.id,
                    fetched_at=fetched_at, data_as_of_status="UNVERIFIABLE", refresh_ranking=False,
                    resolver_client=getattr(provider, "client", None), initial_state=state,
                )
                all_events.extend(day_events)
                hit = set(state.hit_target_indices)
                activated = state.activated
                lifecycle = state.lifecycle_state
                last_ts = state.last_processed_timestamp
                for event in day_events:
                    event_type = str(event.event_type)
                    last_ts = event.event_timestamp
                    if event_type == "ACTIVATED":
                        activated = True; lifecycle = "ACTIVE_POSITION"
                    elif event_type.startswith("TP") and event_type[2:].isdigit():
                        hit.add(int(event_type[2:])); lifecycle = "CLOSED_TARGETS" if len(hit) == len(target_levels) else "ACTIVE_POSITION"
                    elif event_type == "SL":
                        lifecycle = "CLOSED_STOP"
                    elif event_type == "CLOSE":
                        lifecycle = "CLOSED_UNVERIFIABLE"
                    elif event_type == "AMBIGUOUS":
                        lifecycle = "CLOSED_UNVERIFIABLE"
                state = LifecycleState(
                    activated=activated, hit_target_indices=frozenset(hit),
                    remaining_target_indices=frozenset(set(range(1, len(target_levels) + 1)) - hit),
                    current_stop=state.current_stop, lifecycle_state=lifecycle,
                    last_processed_timestamp=last_ts,
                )
                if planner.terminal(state):
                    break
            if planner.terminal(state):
                break

        terminal_states = {"CLOSED_TARGETS", "CLOSED_STOP", "CLOSED_UNVERIFIABLE"}
        terminal_event = next((event for event in reversed(all_events) if str(event.event_type) in {"SL", "AMBIGUOUS"} or str(event.event_type).startswith("TP")), None)
        if state.lifecycle_state == "CLOSED_UNVERIFIABLE":
            run.status = "COMPLETED_UNVERIFIABLE"; run.termination_reason = "LIFECYCLE_UNVERIFIABLE"; run.exit_timestamp = getattr(terminal_event, "event_timestamp", None)
        elif incomplete_after_activation:
            run.status = "REPLAY_PARTIAL"; run.termination_reason = "DATA_TRUNCATED_WHILE_ACTIVE"; run.exit_timestamp = last_actual
        elif state.lifecycle_state in terminal_states:
            run.status = "COMPLETED"; run.termination_reason = "LIFECYCLE_COMPLETED"; run.exit_timestamp = getattr(terminal_event, "event_timestamp", None)
        elif state.activated:
            run.status = "COMPLETED"; run.termination_reason = "HORIZON_REACHED_ACTIVE"; run.exit_timestamp = end_utc
        else:
            run.status = "COMPLETED"; run.termination_reason = "HORIZON_REACHED_UNTRIGGERED"; run.exit_timestamp = end_utc

        coverage_statuses = macro_statuses + (minute_statuses if state.activated else [])
        run.coverage_status = "FULL" if coverage_statuses and not any(status in {"PARTIAL_WINDOW", "GAPPED", "UNAVAILABLE"} for status in coverage_statuses) else ("GAPPED" if "GAPPED" in coverage_statuses else "PARTIAL_WINDOW")
        run.coverage_ratio = (macro_actual / macro_expected) if macro_expected else 0.0
        run.actual_start = first_actual
        run.actual_end = last_actual
        run.provider = "MULTI_SOURCE"
        run.provider_endpoint = "ADAPTIVE_DAILY_1M"
        run.data_source = run.provider
        run.fetched_at = fetched_at
        run.data_as_of_status = "UNVERIFIABLE"
        run.ambiguity_status = "AMBIGUOUS" if any(str(e.event_type) == "AMBIGUOUS" for e in all_events) else "NONE"
        run.quality_status = "UNVERIFIABLE" if run.ambiguity_status == "AMBIGUOUS" else "UNASSESSED"
        run.provider_metadata = {
            "resolution_mode": "HYBRID_MACRO_DRILLDOWN", "macro_interval": "1d",
            "macro_chunk_days": 365, "critical_days_count": len(drilldown_days),
            "critical_days": drilldown_days, "macro_chunks": macro_chunks,
            "current_stop": str(state.current_stop) if state.current_stop is not None else None,
            "remaining_target_indices": sorted(state.remaining_target_indices),
            "last_processed_timestamp": state.last_processed_timestamp.isoformat() if state.last_processed_timestamp else None,
            "requested_start": source_time.isoformat(), "requested_end": end_utc.isoformat(),
        }
        evidences = session.execute(select(HistoricalMarketEvidence).where(HistoricalMarketEvidence.replay_run_id == run.id)).scalars().all()
        for evidence in evidences:
            metadata = dict(evidence.metadata_json or {})
            metadata.update({
                "resolution_mode": "HYBRID_MACRO_DRILLDOWN",
                "macro_chunk_days": 365,
                "critical_days_count": len(drilldown_days),
            })
            evidence.metadata_json = metadata
        if evidences:
            run.dataset_hash = self._artifact_hash({"evidence_hashes": sorted(e.artifact_hash for e in evidences)})
        run.result_json = {
            "event_ids": [event.id for event in all_events], "event_count": len(all_events),
            "events": [{"id": event.id, "type": event.event_type, "timestamp": event.event_timestamp.isoformat(), "price": str(event.price) if event.price is not None else None, "replay_status": event.replay_status, "confidence": str(event.event_confidence), "data": event.event_data} for event in all_events],
            "ambiguity_status": run.ambiguity_status, "lifecycle_status": state.lifecycle_state, "pnl_percentage": "0" if state.lifecycle_state == "PENDING_ORDER" else None,
            "termination_reason": run.termination_reason,
            "exit_timestamp": run.exit_timestamp.isoformat() if run.exit_timestamp else None,
            "evidence_metadata": {"resolution_mode": "HYBRID_MACRO_DRILLDOWN", "macro_chunk_days": 365, "critical_days_count": len(drilldown_days)},
            "source_lifecycle": source_lifecycle,
        }
        run.completed_at = datetime.now(timezone.utc)
        session.flush()
        coverage = self._coverage_from_candles(start=macro_start, end=end_utc, interval="1d", candles=[
            MarketCandle(asset=str(signal.asset or ""), market=signal.market, open_time=t, open=Decimal("1"), high=Decimal("1"), low=Decimal("1"), close=Decimal("1"), volume=Decimal("1"), data_source="ADAPTIVE_MACRO") for t in macro_times
        ])
        return {"run": run, "events": all_events, "status": run.status, "replayed": not created, "coverage": coverage}

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
        retry_of_fingerprint: str | None = None,
        reprocess_of_run_id: int | None = None,
    ) -> dict:
        """Run G6 from an existing G5 materialization; caller owns commit/rollback."""
        self._g5_materialization(session, signal_id=signal_id, materialization_id=materialization_id)
        signal, _, _, target_levels = self._signal_levels(session, signal_id)
        source_lifecycle = self._source_lifecycle(session, signal_id=signal_id)

        # ✅ FIXED (Future Replay Boundary): clamp requested end to 'now' before
        # it flows into the planner/provider. Everything downstream uses end_utc.
        start_utc = self._utc(start)
        requested_replay_end = self._utc(replay_end)
        effective_replay_end = min(
            requested_replay_end,
            datetime.now(timezone.utc),
        )
        end_utc = effective_replay_end

        if start_utc >= end_utc:
            raise HistoricalSignalValidationError("Replay window is invalid or in the future")
        if provider is None:
            from capitalguard.infrastructure.market.historical_ohlcv_provider import BinanceHistoricalOhlcvProvider
            provider = BinanceHistoricalOhlcvProvider()
        if interval == "1m" and hasattr(provider, "fetch_daily") and hasattr(provider, "fetch_minute_day"):
            return self._replay_g6_annual_adaptive(session, signal_id=signal_id, materialization_id=materialization_id, start=start_utc, replay_end=end_utc, limit=limit, provider=provider, retry_of_fingerprint=retry_of_fingerprint, reprocess_of_run_id=reprocess_of_run_id)
        run, created = self._get_or_create_run(session, signal_id=signal_id, materialization_id=materialization_id, start=start_utc, replay_end=end_utc, interval=interval, limit=limit, retry_of_fingerprint=retry_of_fingerprint, reprocess_of_run_id=reprocess_of_run_id)
        if not created and run.status in {"COMPLETED", "COMPLETED_UNVERIFIABLE", "REPLAY_PARTIAL"}:
            events = session.execute(select(HistoricalSignalEvent).where(HistoricalSignalEvent.replay_run_id == run.id).order_by(HistoricalSignalEvent.event_timestamp, HistoricalSignalEvent.id)).scalars().all()
            return {"run": run, "events": events, "status": run.status, "replayed": True}
        if provider is None:
            from capitalguard.infrastructure.market.historical_ohlcv_provider import BinanceHistoricalOhlcvProvider
            provider = BinanceHistoricalOhlcvProvider()
        coverage: HistoricalCoverage
        try:
            if hasattr(provider, "fetch_with_coverage"):
                candles, endpoint, coverage = provider.fetch_with_coverage(asset=str(signal.asset or ""), market=signal.market, interval=interval, start=start_utc, end=end_utc, limit=limit)
            else:
                candles, endpoint = provider.fetch(asset=str(signal.asset or ""), market=signal.market, interval=interval, start=start_utc, end=end_utc, limit=limit)
                coverage = self._coverage_from_candles(start=start_utc, end=end_utc, interval=interval, candles=candles)
        except Exception as exc:
            from capitalguard.infrastructure.market.binance_client import HistoricalMarketProviderError
            if not isinstance(exc, HistoricalMarketProviderError): raise
            run.status = "FAILED"; run.failure_reason = str(exc)[:500]; run.failed_at = datetime.now(timezone.utc); run.quality_status = "UNVERIFIED"; session.flush()
            return {"run": run, "events": [], "status": "FAILED", "failure_reason": str(exc)}
        if not candles:
            run.coverage_status = CoverageStatus.UNAVAILABLE.value; run.coverage_ratio = 0.0; run.status = "REPLAY_PARTIAL"; run.failure_reason = "Historical candle provider returned no evidence"; run.failed_at = datetime.now(timezone.utc); run.quality_status = "UNVERIFIED"; session.flush()
            return {"run": run, "events": [], "status": run.status, "failure_reason": run.failure_reason}
        fetched_at = datetime.now(timezone.utc)
        run.coverage_status = coverage.status.value; run.coverage_ratio = coverage.coverage_ratio; run.actual_start = coverage.actual_start; run.actual_end = coverage.actual_end
        run.provider = sorted({candle.data_source for candle in candles if candle.data_source})[0] if candles else None; run.provider_endpoint = endpoint; run.data_source = run.provider
        run.provider_metadata = {"provider": run.provider, "provider_version": "UNVERIFIED", "endpoint": endpoint, "interval": interval, "limit": limit, "data_as_of_status": "UNVERIFIABLE", "coverage_status": coverage.status.value, "coverage_ratio": coverage.coverage_ratio, "requested_start": start_utc.isoformat(), "requested_end": end_utc.isoformat(), "actual_start": coverage.actual_start.isoformat() if coverage.actual_start else None, "actual_end": coverage.actual_end.isoformat() if coverage.actual_end else None, "expected_candles": coverage.expected_candles, "actual_candles": coverage.actual_candles, "gaps": [[gap_start.isoformat(), gap_end.isoformat()] for gap_start, gap_end in coverage.gaps]}
        run.fetched_at = fetched_at; run.data_as_of_status = "UNVERIFIABLE"
        events = self.replay_candles(session, signal_id=signal_id, candles=candles, replay_end=end_utc, interval=interval, provider_endpoint=endpoint, replay_run_id=run.id, fetched_at=fetched_at, data_as_of_status=run.data_as_of_status, refresh_ranking=False, resolver_client=getattr(provider, "client", None))
        evidence = session.execute(select(HistoricalMarketEvidence).where(HistoricalMarketEvidence.replay_run_id == run.id)).scalars().first()
        run.dataset_hash = evidence.artifact_hash if evidence else self._artifact_hash(self._artifact_payload(signal_id=signal_id, asset=signal.asset, market=signal.market, interval=interval, candles=candles, replay_end=end_utc, provider_endpoint=endpoint))
        run.ambiguity_status = "AMBIGUOUS" if any(event.replay_status == "AMBIGUOUS" for event in events) else "NONE"; run.quality_status = "UNVERIFIABLE" if run.ambiguity_status in {"AMBIGUOUS", "INFERRED"} or run.data_as_of_status != "VERIFIED" else "UNASSESSED"

        # Lifecycle-First Termination: a financially terminal trade remains terminal
        # even when the provider could not cover the remainder of the requested window.
        event_types = [str(getattr(event, "event_type", "")) for event in events]
        target_count = len(target_levels)
        hit_targets = {
            int(event_type[2:])
            for event_type in event_types
            if event_type.startswith("TP") and event_type[2:].isdigit()
        }
        if "SL" in event_types:
            lifecycle_state = "CLOSED_STOP"
            lifecycle_terminal_event = next((event for event in reversed(events) if event.event_type == "SL"), None)
        elif target_count and len(hit_targets) >= target_count:
            lifecycle_state = "CLOSED_TARGETS"
            lifecycle_terminal_event = next((event for event in reversed(events) if event.event_type.startswith("TP") and event.event_type[2:].isdigit()), None)
        elif "CLOSE" in event_types:
            lifecycle_state = "FINAL_CLOSE"
            lifecycle_terminal_event = next((event for event in reversed(events) if event.event_type == "CLOSE"), None)
        else:
            lifecycle_state = "ACTIVE" if "ACTIVATED" in event_types else "NOT_ACTIVATED"
            lifecycle_terminal_event = None

        # OHLCV collision is financially terminal but intra-candle ordering is unknowable.
        # Preserve factual coverage while classifying the lifecycle as an unverifiable close.
        if "AMBIGUOUS" in event_types:
            lifecycle_state = "CLOSED_UNVERIFIABLE"
            lifecycle_terminal_event = next(
                (event for event in reversed(events) if event.event_type == "AMBIGUOUS"),
                None,
            )

        terminal_states = {"CLOSED_TARGETS", "CLOSED_STOP", "FINAL_CLOSE", "CLOSED_UNVERIFIABLE"}
        resolution_quality = (
            "VERIFIED"
            if lifecycle_terminal_event is not None and getattr(lifecycle_terminal_event, "replay_status", None) == "VERIFIED"
            else "UNVERIFIABLE"
        )

        if lifecycle_state in terminal_states:
            run.status = "COMPLETED" if resolution_quality == "VERIFIED" else "COMPLETED_UNVERIFIABLE"
            run.termination_reason = "LIFECYCLE_COMPLETED"
            run.exit_timestamp = getattr(lifecycle_terminal_event, "event_timestamp", None)
        elif lifecycle_state == "NOT_ACTIVATED" and coverage.status == CoverageStatus.FULL:
            run.status = "COMPLETED"
            run.termination_reason = "HORIZON_REACHED_UNTRIGGERED"
            run.exit_timestamp = end_utc
        elif coverage.status in {CoverageStatus.PARTIAL_WINDOW, CoverageStatus.GAPPED}:
            run.status = "REPLAY_PARTIAL"
            run.termination_reason = "DATA_TRUNCATED_WHILE_ACTIVE"
            run.exit_timestamp = coverage.actual_end
        else:
            run.status = "REPLAY_PARTIAL"
            run.termination_reason = "DATA_TRUNCATED_WHILE_ACTIVE"
            run.exit_timestamp = coverage.actual_end

        run.result_json = {"event_ids": [event.id for event in events], "event_count": len(events), "events": [{"id": event.id, "type": event.event_type, "timestamp": event.event_timestamp.isoformat(), "price": str(event.price) if event.price is not None else None, "replay_status": event.replay_status, "confidence": str(event.event_confidence), "data": event.event_data} for event in events], "evidence_id": evidence.id if evidence else None, "ambiguity_status": run.ambiguity_status, "lifecycle_status": lifecycle_state, "resolution_quality": resolution_quality, "activation_risk": bool(lifecycle_state == "NOT_ACTIVATED" and coverage.status != CoverageStatus.FULL), "entry_ambiguity": bool(lifecycle_state == "NOT_ACTIVATED" and coverage.status != CoverageStatus.FULL), "termination_reason": run.termination_reason, "exit_timestamp": run.exit_timestamp.isoformat() if run.exit_timestamp else None, "coverage": {"status": coverage.status.value, "ratio": coverage.coverage_ratio, "requested_start": start_utc.isoformat(), "requested_end": end_utc.isoformat(), "actual_start": coverage.actual_start.isoformat() if coverage.actual_start else None, "actual_end": coverage.actual_end.isoformat() if coverage.actual_end else None, "expected_candles": coverage.expected_candles, "actual_candles": coverage.actual_candles, "gaps": [[gap_start.isoformat(), gap_end.isoformat()] for gap_start, gap_end in coverage.gaps]}, "source_lifecycle": source_lifecycle}
        run.completed_at = datetime.now(timezone.utc); session.flush()
        return {"run": run, "events": events, "status": run.status, "replayed": not created, "coverage": coverage}

    def replay(self, session: Session, *, signal_id: int, observations: Iterable[MarketObservation], replay_end: datetime, replay_run_id: int | None = None, refresh_ranking: bool = True) -> list[HistoricalSignalEvent]:
        signal, entry, stop, target_levels = self._signal_levels(session, signal_id); end_time = self._utc(replay_end); normalized=[]
        for observation in observations:
            timestamp=self._utc(observation.as_of); price=self._decimal(observation.price)
            if price is None or price <= 0: raise HistoricalSignalValidationError("Market observation price must be positive")
            if timestamp > end_time: raise HistoricalSignalValidationError("Market observation is after replay_end")
            if observation.asset.upper() != str(signal.asset or "").upper(): continue
            if signal.market and observation.market and observation.market.upper() != signal.market.upper(): continue
            normalized.append(MarketObservation(observation.asset.upper(), observation.market, timestamp, price, observation.data_source))
        normalized.sort(key=lambda item: item.as_of); events=[]; activated=False; closed=False; hit_targets=set(); dedup_prefix=f"g6:{replay_run_id}" if replay_run_id is not None else f"replay:{signal.id}"; clock=SimulationClock(self._utc(signal.decision_timestamp))
        for observation in normalized:
            clock.advance_to(observation.as_of)
            if observation.as_of < self._utc(signal.decision_timestamp) or closed: continue
            if not activated and entry is not None and self._hit(signal.side, observation.price, entry):
                events.append(self.signal_service.record_event(session, signal_id=signal.id, event_type="ACTIVATED", event_timestamp=clock.current_time, market_as_of=clock.current_time, data_source=observation.data_source, price=observation.price, replay_status="VERIFIED", event_confidence="1.0000", event_data={"replay_end": end_time.isoformat(), "replay_run_id": replay_run_id}, dedup_key=f"{dedup_prefix}:ACTIVATED", replay_run_id=replay_run_id, refresh_ranking=refresh_ranking)); activated=True
            if not activated: continue
            if stop is not None and self._stop_hit(signal.side, observation.price, stop):
                events.append(self.signal_service.record_event(session, signal_id=signal.id, event_type="SL", event_timestamp=clock.current_time, market_as_of=clock.current_time, data_source=observation.data_source, price=stop, replay_status="VERIFIED", event_confidence="1.0000", event_data={"replay_end": end_time.isoformat(), "replay_run_id": replay_run_id}, dedup_key=f"{dedup_prefix}:SL", replay_run_id=replay_run_id, refresh_ranking=refresh_ranking)); closed=True; continue
            for index, level in enumerate(target_levels, start=1):
                if index in hit_targets or not self._hit(signal.side, observation.price, level): continue
                events.append(self.signal_service.record_event(session, signal_id=signal.id, event_type=f"TP{index}", event_timestamp=clock.current_time, market_as_of=clock.current_time, data_source=observation.data_source, price=level, replay_status="VERIFIED", event_confidence="1.0000", event_data={"target_index": index, "replay_end": end_time.isoformat(), "replay_run_id": replay_run_id}, dedup_key=f"{dedup_prefix}:TP{index}", replay_run_id=replay_run_id, refresh_ranking=refresh_ranking)); hit_targets.add(index)
            if target_levels and len(hit_targets)==len(target_levels): closed=True
        return events

    @staticmethod
    def _coarse_to_fine_candles(candles: list[MarketCandle], *, side: str | None, entry: Decimal | None, stop: Decimal | None, target_levels: list[Decimal]) -> list[MarketCandle]:
        """1D -> 1H -> 1m traversal over already-fetched OHLCV with directional level semantics."""
        if not candles:
            return []
        direction = str(side or "").upper()
        if direction not in {"LONG", "SHORT"}:
            return []

        days: dict[object, list[MarketCandle]] = {}
        for candle in candles:
            timestamp = candle.open_time.astimezone(timezone.utc)
            days.setdefault(timestamp.date(), []).append(candle)

        def range_touches(high: Decimal, low: Decimal, level: Decimal, *, is_stop: bool = False) -> bool:
            if direction == "LONG":
                return low <= level if is_stop else high >= level
            return high >= level if is_stop else low <= level

        selected: list[MarketCandle] = []
        activated = False
        for day in sorted(days):
            day_candles = sorted(days[day], key=lambda item: item.open_time)
            day_high = max(c.high for c in day_candles)
            day_low = min(c.low for c in day_candles)
            if not activated:
                if entry is None or not range_touches(day_high, day_low, entry):
                    continue
            else:
                relevant = False
                if stop is not None and range_touches(day_high, day_low, stop, is_stop=True):
                    relevant = True
                if any(range_touches(day_high, day_low, level) for level in target_levels):
                    relevant = True
                if not relevant:
                    continue

            hours: dict[int, list[MarketCandle]] = {}
            for candle in day_candles:
                hours.setdefault(candle.open_time.astimezone(timezone.utc).hour, []).append(candle)
            for hour in sorted(hours):
                hour_candles = sorted(hours[hour], key=lambda item: item.open_time)
                high = max(c.high for c in hour_candles)
                low = min(c.low for c in hour_candles)
                if not activated:
                    if entry is None or not range_touches(high, low, entry):
                        continue
                    selected.extend(hour_candles)
                    activated = True
                    continue

                stop_touch = stop is not None and range_touches(high, low, stop, is_stop=True)
                target_touch = any(range_touches(high, low, level) for level in target_levels)
                if stop_touch or target_touch:
                    selected.extend(hour_candles)
        return sorted(selected, key=lambda item: item.open_time)

    def replay_candles(self, session: Session, *, signal_id: int, candles: Iterable[MarketCandle], replay_end: datetime, interval: str = "1m", provider_endpoint: str | None = None, replay_run_id: int | None = None, fetched_at: datetime | None = None, data_as_of_status: str = "UNVERIFIABLE", refresh_ranking: bool = True, resolver_client=None, initial_state: LifecycleState | None = None) -> list[HistoricalSignalEvent]:
        signal, entry, stop, target_levels = self._signal_levels(session, signal_id); end_time=self._utc(replay_end); normalized=[]
        if initial_state is not None:
            stop = initial_state.current_stop if initial_state.current_stop is not None else stop
        for candle in candles:
            timestamp=self._utc(candle.open_time)
            if timestamp > end_time: raise HistoricalSignalValidationError("Market candle is after replay_end")
            if candle.asset.upper() != str(signal.asset or "").upper(): continue
            if signal.market and candle.market and candle.market.upper() != signal.market.upper(): continue
            values=[self._decimal(value) for value in (candle.open,candle.high,candle.low,candle.close,candle.volume)]
            if any(value is None or value <= 0 for value in values): raise HistoricalSignalValidationError("OHLCV candle values must be positive")
            normalized.append(candle)
        normalized.sort(key=lambda item:self._utc(item.open_time)); events=[]; activated=bool(initial_state.activated) if initial_state is not None else False; closed=False; hit_targets=set(initial_state.hit_target_indices) if initial_state is not None else set(); dedup_prefix=f"g6:{replay_run_id}" if replay_run_id is not None else f"replay:{signal.id}"; decision_time=self._utc(signal.decision_timestamp); clock=SimulationClock(decision_time)
        eligible_source=[candle for candle in normalized if self._utc(candle.open_time)>=decision_time]; eligible_candles=sorted(eligible_source, key=lambda item:self._utc(item.open_time)) if initial_state is not None and initial_state.activated else self._coarse_to_fine_candles(eligible_source, side=signal.side, entry=entry, stop=stop, target_levels=target_levels); ambiguity_status="NONE"
        market_evidence=self._record_market_evidence(session, signal_id=signal.id, asset=signal.asset, market=signal.market, interval=interval, candles=normalized, replay_end=end_time, provider_endpoint=provider_endpoint, replay_run_id=replay_run_id, fetched_at=fetched_at, data_as_of_status=data_as_of_status, ambiguity_status=ambiguity_status, quality_status="UNASSESSED")
        for candle in eligible_candles:
            clock.advance_to(self._utc(candle.open_time)); candle_time=clock.current_time
            if closed: continue
            activation_hit=entry is not None and (candle.high>=entry if str(signal.side or "").upper()=="LONG" else candle.low<=entry)
            if not activated and activation_hit:
                events.append(self.signal_service.record_event(session, signal_id=signal.id, event_type="ACTIVATED", event_timestamp=candle_time, market_as_of=candle_time, data_source=candle.data_source, price=entry, replay_status="VERIFIED", event_confidence="1.0000", event_data={"replay_end":end_time.isoformat(),"candle_rule":"OHLCV","market_evidence_ref":market_evidence.replay_run_ref if market_evidence else None,"replay_run_id":replay_run_id}, dedup_key=f"{dedup_prefix}:ACTIVATED", replay_run_id=replay_run_id, refresh_ranking=refresh_ranking)); activated=True
            if not activated: continue
            stop_hit=stop is not None and self._candle_stop_hit(signal.side,candle,stop); target_hits=[index for index,level in enumerate(target_levels,start=1) if index not in hit_targets and self._candle_target_hit(signal.side,candle,level)]
            if stop_hit and target_hits and replay_run_id is None:
                events.append(self.signal_service.record_event(session,signal_id=signal.id,event_type="SL",event_timestamp=candle_time,market_as_of=candle_time,data_source=candle.data_source,price=stop,replay_status="VERIFIED",event_confidence="1.0000",event_data={"replay_end":end_time.isoformat(),"candle_rule":"PESSIMISTIC_SL_FIRST","market_evidence_ref":market_evidence.replay_run_ref if market_evidence else None,"replay_run_id":replay_run_id},dedup_key=f"{dedup_prefix}:SL",replay_run_id=replay_run_id,refresh_ranking=refresh_ranking)); closed=True; continue
            if stop_hit and target_hits and replay_run_id is not None:
                if self.intra_candle_resolver is None and resolver_client is not None and hasattr(resolver_client,"fetch_agg_trades"): self.intra_candle_resolver=IntraCandleResolver(resolver_client)
                if self.intra_candle_resolver is None:
                    ambiguity_status="AMBIGUOUS"
                    events.append(self.signal_service.record_event(session,signal_id=signal.id,event_type="AMBIGUOUS",event_timestamp=candle_time,market_as_of=candle_time,data_source=candle.data_source,price=None,replay_status="AMBIGUOUS",event_confidence="0.0000",event_data={"replay_end":end_time.isoformat(),"candle_rule":"NO_FINE_GRAIN_PROVIDER","possible_events":["SL",*[f"TP{index}" for index in target_hits]],"high":str(candle.high),"low":str(candle.low),"market_evidence_ref":market_evidence.replay_run_ref if market_evidence else None,"replay_run_id":replay_run_id},dedup_key=f"{dedup_prefix}:AMBIGUOUS:{candle_time.isoformat()}",replay_run_id=replay_run_id,refresh_ranking=refresh_ranking)); closed=True; continue
                resolution=self.intra_candle_resolver.resolve(symbol=str(signal.asset or ""),market=signal.market,side=str(signal.side or ""),candle_open=candle_time,candle_close=candle_time+interval_delta(interval),stop=stop,target_levels=list(enumerate(target_levels,start=1)),candle_high=candle.high,candle_low=candle.low); event_type=resolution.event; event_price=stop if event_type=="SL" else target_levels[int(event_type[2:])-1]; replay_status="VERIFIED" if resolution.resolution=="VERIFIED_EVENT" else "INFERRED"; ambiguity_status="NONE" if resolution.resolution=="VERIFIED_EVENT" else "INFERRED"
                # ✅ RESTORED (D2): both keys preserved for backward compatibility with existing consumers.
                events.append(self.signal_service.record_event(session,signal_id=signal.id,event_type=event_type,event_timestamp=candle_time,market_as_of=candle_time,data_source=candle.data_source,price=event_price,replay_status=replay_status,event_confidence=str(resolution.confidence),event_data={"replay_end":end_time.isoformat(),"candle_rule":resolution.resolution,"resolution":resolution.resolution,"reason":resolution.reason,"resolution_details":resolution.details,"market_evidence_ref":market_evidence.replay_run_ref if market_evidence else None,"replay_run_id":replay_run_id},dedup_key=f"{dedup_prefix}:{event_type}:{candle_time.isoformat()}",replay_run_id=replay_run_id,refresh_ranking=refresh_ranking)); closed=True; continue
            if stop_hit:
                events.append(self.signal_service.record_event(session,signal_id=signal.id,event_type="SL",event_timestamp=candle_time,market_as_of=candle_time,data_source=candle.data_source,price=stop,replay_status="VERIFIED",event_confidence="1.0000",event_data={"replay_end":end_time.isoformat(),"candle_rule":"OHLCV","market_evidence_ref":market_evidence.replay_run_ref if market_evidence else None,"replay_run_id":replay_run_id},dedup_key=f"{dedup_prefix}:SL",replay_run_id=replay_run_id,refresh_ranking=refresh_ranking)); closed=True; continue
            for index in target_hits:
                level=target_levels[index-1]
                events.append(self.signal_service.record_event(session,signal_id=signal.id,event_type=f"TP{index}",event_timestamp=candle_time,market_as_of=candle_time,data_source=candle.data_source,price=level,replay_status="VERIFIED",event_confidence="1.0000",event_data={"target_index":index,"replay_end":end_time.isoformat(),"candle_rule":"OHLCV","market_evidence_ref":market_evidence.replay_run_ref if market_evidence else None,"replay_run_id":replay_run_id},dedup_key=f"{dedup_prefix}:TP{index}",replay_run_id=replay_run_id,refresh_ranking=refresh_ranking)); hit_targets.add(index)
            if target_levels and len(hit_targets)==len(target_levels): closed=True
        if market_evidence is not None:
            metadata=dict(market_evidence.metadata_json or {}); metadata["ambiguity_status"]=ambiguity_status; metadata["quality_status"]="UNVERIFIABLE" if ambiguity_status=="AMBIGUOUS" else "UNASSESSED"; market_evidence.metadata_json=metadata; session.flush()
        return events

    def replay_from_binance(self, session: Session, *, signal_id: int, start: datetime, replay_end: datetime, interval: str = "1m", limit: int = 1500, provider=None, replay_run_id: int | None = None, refresh_ranking: bool = True, fetched_at: datetime | None = None, data_as_of_status: str = "UNVERIFIABLE") -> list[HistoricalSignalEvent]:
        signal, _, _, _ = self._signal_levels(session, signal_id)
        if provider is None:
            from capitalguard.infrastructure.market.historical_ohlcv_provider import BinanceHistoricalOhlcvProvider
            provider=BinanceHistoricalOhlcvProvider()
        candles, endpoint=provider.fetch(asset=str(signal.asset or ""),market=signal.market,interval=interval,start=self._utc(start),end=self._utc(replay_end),limit=limit)
        if not candles: raise HistoricalSignalValidationError("Historical candle provider returned no evidence")
        return self.replay_candles(session,signal_id=signal_id,candles=candles,replay_end=replay_end,interval=interval,provider_endpoint=endpoint,replay_run_id=replay_run_id,fetched_at=fetched_at,data_as_of_status=data_as_of_status,refresh_ranking=refresh_ranking)

# --- END OF FILE ---