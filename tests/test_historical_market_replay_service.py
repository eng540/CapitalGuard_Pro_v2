from datetime import datetime, timezone
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from capitalguard.application.services.historical_market_replay_service import (
    HistoricalMarketReplayService,
    MarketCandle,
    MarketObservation,
)
from capitalguard.application.services.historical_signal_service import HistoricalSignalService, HistoricalSignalValidationError
from capitalguard.application.services.web_command_service import WebCommandError, WebCommandService
from capitalguard.config import settings
from capitalguard.infrastructure.db.models import HistoricalImportBatch, HistoricalMarketEvidence, HistoricalSignalEvent, WebCommandAudit
from capitalguard.infrastructure.db.repository import UserRepository
from capitalguard.infrastructure.market.binance_client import HistoricalMarketProviderError


def _time(day: int, hour: int):
    return datetime(2026, 1, day, hour, 0, tzinfo=timezone.utc)


def _reviewed_batch_signal(db_session, telegram_id: int):
    owner = UserRepository(db_session).find_or_create(telegram_id=telegram_id, first_name="Replay owner")
    batch = HistoricalImportBatch(batch_ref=f"HB-TEST-{telegram_id}", source_kind="FORWARD", requested_by_user_id=owner.id, status="EVIDENCE_INGESTED", manifest_hash=f"manifest-{telegram_id}")
    db_session.add(batch)
    db_session.flush()
    signal_service = HistoricalSignalService()
    evidence = signal_service.ingest_evidence(db_session, source_kind="AUTHORIZED_USER_HISTORY", message_timestamp=_time(2, 9), raw_text="#BTCUSDT LONG Entry 100 Stop 90")
    evidence.batch_id = batch.id
    evidence.ownership_proof_ref = "test://reviewed/ownership"
    signal = signal_service.create_signal(db_session, evidence_id=evidence.id, decision_timestamp=_time(2, 9), asset="BTCUSDT", side="LONG", entry=Decimal("100"), stop_loss=Decimal("90"), targets=[])
    db_session.flush()
    return batch, signal


def test_reviewed_batch_replay_derives_window_and_replays_idempotently(db_session, monkeypatch):
    telegram_id = 97001
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", str(telegram_id))
    batch, signal = _reviewed_batch_signal(db_session, telegram_id)
    calls = []

    def replay(_self, _session, **kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(HistoricalMarketReplayService, "replay_from_binance", replay)
    service = WebCommandService()
    first = service.replay_reviewed_batch_from_binance(db_session, actor_telegram_id=telegram_id, batch_id=batch.id, idempotency_key="batch-replay-once")
    again = service.replay_reviewed_batch_from_binance(db_session, actor_telegram_id=telegram_id, batch_id=batch.id, idempotency_key="batch-replay-once")

    assert first["replayed"] is True
    assert again == first
    assert len(calls) == 1
    assert calls[0]["signal_id"] == signal.id
    assert calls[0]["start"] == signal.decision_timestamp - timedelta(hours=24)
    assert calls[0]["replay_end"] == signal.decision_timestamp
    assert calls[0]["interval"] == "1m"
    assert calls[0]["limit"] == 1500


def test_reviewed_batch_replay_rejects_binance_unavailability_without_success_audit(db_session, monkeypatch):
    telegram_id = 97002
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", str(telegram_id))
    batch, _signal = _reviewed_batch_signal(db_session, telegram_id)

    def unavailable(_self, _session, **_kwargs):
        raise HistoricalMarketProviderError("provider unavailable")

    monkeypatch.setattr(HistoricalMarketReplayService, "replay_from_binance", unavailable)
    with pytest.raises(WebCommandError, match="source unavailable"):
        WebCommandService().replay_reviewed_batch_from_binance(db_session, actor_telegram_id=telegram_id, batch_id=batch.id, idempotency_key="batch-replay-provider-failure")

    assert db_session.execute(select(WebCommandAudit).where(WebCommandAudit.idempotency_key == "batch-replay-provider-failure")).scalar_one_or_none() is None


def test_reviewed_batch_replay_rolls_back_partial_events_artifacts_and_audit_when_binance_fails(db_session, monkeypatch):
    telegram_id = 97003
    idempotency_key = "batch-replay-atomic-provider-failure"
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", str(telegram_id))
    batch, signal = _reviewed_batch_signal(db_session, telegram_id)

    def partially_write_then_fail(_self, session, **_kwargs):
        session.add(HistoricalSignalEvent(
            signal_id=signal.id,
            event_type="ACTIVATED",
            event_timestamp=signal.decision_timestamp,
            market_as_of=signal.decision_timestamp,
            data_source="BINANCE_FUTURES",
            replay_status="VERIFIED",
            event_confidence=Decimal("1"),
            dedup_key="test:partial-binance-event",
        ))
        session.add(HistoricalMarketEvidence(
            signal_id=signal.id,
            replay_run_ref="HMKT-TEST-PARTIAL",
            provider="BINANCE_FUTURES",
            asset="BTCUSDT",
            interval="1m",
            range_start=signal.decision_timestamp,
            range_end=signal.decision_timestamp,
            candle_count=1,
            artifact_hash="f" * 64,
            artifact_key="test:partial-binance-artifact",
        ))
        session.flush()
        raise HistoricalMarketProviderError("provider unavailable")

    monkeypatch.setattr(HistoricalMarketReplayService, "replay_from_binance", partially_write_then_fail)
    with pytest.raises(WebCommandError, match="source unavailable"):
        try:
            WebCommandService().replay_reviewed_batch_from_binance(db_session, actor_telegram_id=telegram_id, batch_id=batch.id, idempotency_key=idempotency_key)
        except WebCommandError:
            # Same transaction boundary used by the WebApp endpoint's session_scope.
            db_session.rollback()
            raise

    assert db_session.execute(select(HistoricalSignalEvent).where(HistoricalSignalEvent.dedup_key == "test:partial-binance-event")).scalar_one_or_none() is None
    assert db_session.execute(select(HistoricalMarketEvidence).where(HistoricalMarketEvidence.artifact_key == "test:partial-binance-artifact")).scalar_one_or_none() is None
    assert db_session.execute(select(WebCommandAudit).where(WebCommandAudit.idempotency_key == idempotency_key)).scalar_one_or_none() is None


def test_replay_records_activation_targets_and_eligibility(db_session):
    signal_service = HistoricalSignalService()
    evidence = signal_service.ingest_evidence(
        db_session,
        source_kind="AUTHORIZED_USER_HISTORY",
        message_timestamp=_time(1, 9),
        raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110@50% TP2 120@50%",
    )
    signal = signal_service.create_signal(
        db_session,
        evidence_id=evidence.id,
        decision_timestamp=_time(1, 9),
        analyst_id=1,
        asset="BTCUSDT",
        side="LONG",
        entry=Decimal("100"),
        stop_loss=Decimal("90"),
        targets=[{"price": "110", "close_percent": 50}, {"price": "120", "close_percent": 50}],
    )
    attribution = signal_service.add_attribution(
        db_session,
        signal_id=signal.id,
        attribution_kind="ANALYST",
        analyst_id=1,
        proof_type="ANALYST_ACCOUNT_CONFIRMATION",
        proof_ref="test://analyst/1",
        confidence_score="1.0000",
        dedup_key=f"test:analyst:{signal.id}",
    )
    signal_service.review_attribution(
        db_session,
        attribution_id=attribution.id,
        reviewer_user_id=99,
        status="VERIFIED",
        note="Test analyst attribution reviewed",
    )
    events = HistoricalMarketReplayService().replay_candles(
        db_session,
        signal_id=signal.id,
        replay_end=_time(1, 15),
        candles=[
            MarketCandle("BTCUSDT", "Futures", _time(1, 10), Decimal("100"), Decimal("100"), Decimal("99"), Decimal("100"), Decimal("1"), "historical-test"),
            MarketCandle("BTCUSDT", "Futures", _time(1, 11), Decimal("100"), Decimal("110"), Decimal("101"), Decimal("110"), Decimal("1"), "historical-test"),
            MarketCandle("BTCUSDT", "Futures", _time(1, 12), Decimal("110"), Decimal("120"), Decimal("111"), Decimal("120"), Decimal("1"), "historical-test"),
        ],
    )

    assert [event.event_type for event in events] == ["ACTIVATED", "TP1", "TP2"]
    assert all(event.replay_status == "VERIFIED" for event in events)
    assert signal.eligible_for_ranking is True
    artifact = db_session.execute(select(HistoricalMarketEvidence).where(HistoricalMarketEvidence.signal_id == signal.id)).scalar_one()
    assert artifact.provider == "historical-test"
    assert artifact.interval == "1m"
    assert artifact.candle_count == 3
    assert artifact.artifact_hash
    assert events[0].event_data["market_evidence_ref"] == artifact.replay_run_ref


def test_replay_rejects_observation_after_replay_end(db_session):
    signal_service = HistoricalSignalService()
    evidence = signal_service.ingest_evidence(
        db_session,
        source_kind="TELEGRAM_EXPORT",
        message_timestamp=_time(1, 9),
        raw_text="#ETHUSDT LONG Entry 100",
    )
    signal = signal_service.create_signal(
        db_session,
        evidence_id=evidence.id,
        decision_timestamp=_time(1, 9),
        asset="ETHUSDT",
        side="LONG",
        entry=Decimal("100"),
    )

    with pytest.raises(HistoricalSignalValidationError, match="replay_end"):
        HistoricalMarketReplayService().replay(
            db_session,
            signal_id=signal.id,
            replay_end=_time(1, 10),
            observations=[MarketObservation("ETHUSDT", "Futures", _time(1, 11), Decimal("101"), "test")],
        )
