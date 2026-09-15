from datetime import timedelta

from sqlalchemy import select

from capitalguard.application.services.historical_forwarding_service import HistoricalForwardingService
from capitalguard.application.services.historical_market_replay_service import HistoricalMarketReplayService
from capitalguard.application.services.historical_replay_decision_service import HistoricalReplayDecisionAuthority
from capitalguard.infrastructure.db.models import HistoricalReplayRun, HistoricalSignalEvent, HistoricalMarketEvidence, HistoricalSignal

from tests.test_historical_forwarding_auto_progression import _auto_batch, FakeProvider, SOURCE_TIME


def test_diagnostic_duplicate_partial_root_cause_r2(db_session):
    service = HistoricalForwardingService()
    batch, receipt, _, _ = _auto_batch(
        db_session,
        raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures",
    )

    first = service.auto_progress_canonical_batch(
        db_session,
        batch_id=batch.id,
        replay_end=SOURCE_TIME + timedelta(days=9),
        limit=1,
        provider=FakeProvider([
            __import__("capitalguard.application.services.historical_market_replay_service", fromlist=["MarketCandle"]).MarketCandle(
                asset="BTCUSDT", market="Futures", open_time=SOURCE_TIME,
                open=100, high=101, low=99, close=100, volume=1, data_source="FAKE"
            )
        ]),
    )
    print("R2_STEP1", first)

    runs_before = db_session.execute(select(HistoricalReplayRun).order_by(HistoricalReplayRun.id)).scalars().all()
    print("R2_RUNS_BEFORE", [
        {"id": r.id, "status": r.status, "fingerprint": r.request_fingerprint,
         "reprocess_of_run_id": r.reprocess_of_run_id,
         "window_start": str(r.window_start), "window_start_tz": str(r.window_start.tzinfo) if r.window_start else None,
         "window_end": str(r.window_end), "window_end_tz": str(r.window_end.tzinfo) if r.window_end else None}
        for r in runs_before
    ])

    resolution = service._duplicate_resolution(db_session, receipt) if hasattr(service, "_duplicate_resolution") else None
    print("R2_DUPLICATE_RESOLUTION", resolution)
    authority = HistoricalReplayDecisionAuthority()
    previous = runs_before[-1] if runs_before else None
    decision = authority.decide(previous)
    print("R2_DECISION", decision)

    replay_service = HistoricalMarketReplayService()
    try:
        result = replay_service.retry_g6(
            db_session,
            receipt_id=receipt.id,
            provider=FakeProvider([
                __import__("capitalguard.application.services.historical_market_replay_service", fromlist=["MarketCandle"]).MarketCandle(
                    asset="BTCUSDT", market="Futures", open_time=SOURCE_TIME,
                    open=100, high=101, low=99, close=100, volume=1, data_source="FAKE"
                )
            ]),
        )
        print("R2_RETRY_RESULT", result)
    except Exception as exc:
        import traceback
        print("R2_RETRY_EXCEPTION_TYPE", type(exc).__name__)
        print("R2_RETRY_EXCEPTION", repr(exc))
        traceback.print_exc()
        print("R2_SESSION_IN_TRANSACTION", db_session.in_transaction())
        print("R2_SESSION_IN_NESTED", db_session.in_nested_transaction())

    runs_after = db_session.execute(select(HistoricalReplayRun).order_by(HistoricalReplayRun.id)).scalars().all()
    signals = db_session.execute(select(HistoricalSignal).order_by(HistoricalSignal.id)).scalars().all()
    events = db_session.execute(select(HistoricalSignalEvent).order_by(HistoricalSignalEvent.id)).scalars().all()
    evidence = db_session.execute(select(HistoricalMarketEvidence).order_by(HistoricalMarketEvidence.id)).scalars().all()
    print("R2_RUNS_AFTER", [
        {"id": r.id, "status": r.status, "fingerprint": r.request_fingerprint,
         "reprocess_of_run_id": r.reprocess_of_run_id,
         "signal_id": r.signal_id, "materialization_id": r.materialization_id,
         "failure_reason": r.failure_reason}
        for r in runs_after
    ])
    print("R2_COUNTS", {"signals": len(signals), "events": len(events), "evidence": len(evidence)})
    print("R2_EVENTS", [
        {"id": e.id, "run_id": e.replay_run_id, "type": e.event_type,
         "dedup_key": e.dedup_key, "timestamp": str(e.event_timestamp)}
        for e in events
    ])
    print("R2_EVIDENCE", [
        {"id": e.id, "run_id": e.replay_run_id, "artifact_key": e.artifact_key}
        for e in evidence
    ])

    assert False, "temporary diagnostic: capture R2 duplicate REPROCESS failure above"
