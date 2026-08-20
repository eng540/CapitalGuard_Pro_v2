from capitalguard.application.services.historical_replay_gate_service import HistoricalReplayGateService
from capitalguard.application.services.historical_outcome_reconciliation_service import TimelineEventInput
from datetime import datetime, timedelta, timezone


BASE = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def test_gate_blocks_incomplete_parse():
    report = HistoricalReplayGateService().assess(parse_status="PARTIAL", market_data_available=True)
    assert report.status == "BLOCKED_PARSE_INCOMPLETE"
    assert report.replay_allowed is False


def test_gate_keeps_complete_signal_pending_without_ohlcv():
    report = HistoricalReplayGateService().assess(parse_status="PARSED", market_data_available=False)
    assert report.status == "REPLAY_PENDING"
    assert report.reputation_eligible is False
    assert "HISTORICAL_OHLCV_MISSING" in report.reason_codes


def test_gate_requires_owner_review_for_mismatch():
    report = HistoricalReplayGateService().assess(
        parse_status="PARSED",
        financial_outcome={
            "status": "MISMATCH",
            "warnings": ["FINANCIAL_CONSISTENCY_REVIEW"],
        },
        market_data_available=True,
    )
    assert report.status == "OWNER_REVIEW_REQUIRED"
    assert report.replay_allowed is True
    assert report.reputation_eligible is False


def test_gate_allows_replay_and_reputation_when_all_inputs_are_consistent():
    report = HistoricalReplayGateService().assess(
        parse_status="PARSED",
        timeline_events=[
            TimelineEventInput("ACTIVATED", BASE),
            TimelineEventInput("TP1", BASE + timedelta(minutes=1)),
            TimelineEventInput("CLOSE", BASE + timedelta(minutes=2)),
        ],
        market_data_available=True,
    )
    assert report.status == "REPLAY_READY"
    assert report.replay_allowed is True
    assert report.reputation_eligible is True
