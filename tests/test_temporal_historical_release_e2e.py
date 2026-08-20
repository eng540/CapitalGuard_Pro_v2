from datetime import datetime, timedelta, timezone

from capitalguard.application.services.historical_outcome_reconciliation_service import (
    HistoricalOutcomeReconciliationService,
    TimelineEventInput,
)
from capitalguard.application.services.historical_replay_gate_service import HistoricalReplayGateService
from capitalguard.application.services.live_review_service import LiveReviewService
from capitalguard.application.services.release_stability_gate_service import (
    ReleaseStabilityGateService,
    StabilityGateInput,
)
from capitalguard.application.services.temporal_decision_service import TemporalDecisionService
from capitalguard.application.services.temporal_normalizer import TemporalContext


BASE = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
PAYLOAD = {
    "asset": "BTCUSDT",
    "side": "LONG",
    "entry": "69000",
    "stop_loss": "68000",
    "targets": [{"price": "70000", "close_percent": "100"}],
}


def temporal(age_seconds):
    return TemporalContext(
        source_time=BASE,
        event_time=BASE,
        received_time=BASE + timedelta(seconds=age_seconds),
        ingested_time=BASE + timedelta(seconds=age_seconds),
        edit_time=None,
        source_chat_id=-100123,
        source_message_id=1,
        source_origin_type="CHANNEL",
        source_message_revision=0,
        source_time_verified=True,
    )


def test_realistic_temporal_paths_stay_separated():
    service = TemporalDecisionService()
    fresh = service.decide(
        temporal=temporal(10),
        parsed_payload=PAYLOAD,
        current_price="69100",
        market_data_available=True,
    )
    stale = service.decide(
        temporal=temporal(545),
        parsed_payload=PAYLOAD,
        current_price=None,
        market_data_available=False,
    )
    old = service.decide(
        temporal=temporal(7200),
        parsed_payload=PAYLOAD,
        current_price=None,
        market_data_available=False,
    )
    assert fresh.mode == "LIVE_ELIGIBLE"
    assert stale.mode == "LIVE_STALE"
    assert old.mode == "HISTORICAL_RECONSTRUCTION"
    assert LiveReviewService().prepare(fresh.as_dict()).creates_live_entity is False
    assert LiveReviewService().prepare(stale.as_dict()).creates_live_entity is False
    assert LiveReviewService().prepare(old.as_dict()).creates_live_entity is False


def test_closed_conflict_stops_reputation_before_replay():
    outcome = HistoricalOutcomeReconciliationService().check_reported_outcome(
        side="LONG", entry="0.21000", exit_price="0.19500", reported_pnl_pct="0.95"
    )
    gate = HistoricalReplayGateService().assess(
        parse_status="PARSED",
        financial_outcome={"status": outcome.status, "warnings": list(outcome.warnings)},
        timeline_events=[TimelineEventInput("ACTIVATED", BASE), TimelineEventInput("CLOSE", BASE + timedelta(minutes=1))],
        market_data_available=True,
    )
    assert gate.status == "OWNER_REVIEW_REQUIRED"
    assert gate.replay_allowed is True
    assert gate.reputation_eligible is False


def test_release_gate_holds_on_leak_or_outbox_and_passes_safe_snapshot():
    service = ReleaseStabilityGateService()
    hold = service.evaluate(StabilityGateInput(186, 0, 1, 0, 1, 0, 0))
    assert hold.status == "HOLD"
    assert "LIVE_ENTITY_LEAK_DETECTED" in hold.reasons
    passed = service.evaluate(StabilityGateInput(186, 0, 1, 0, 0, 0, 0))
    assert passed.status == "PASS"
    assert passed.commercial_enabled is False
    assert passed.copy_trading_enabled is False
