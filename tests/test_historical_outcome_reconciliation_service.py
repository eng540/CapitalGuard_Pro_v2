from datetime import datetime, timedelta, timezone
from decimal import Decimal

from capitalguard.application.services.historical_outcome_reconciliation_service import (
    HistoricalOutcomeReconciliationService,
    TimelineEventInput,
)


BASE = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def test_lsk_source_result_is_flagged_against_long_price_move():
    report = HistoricalOutcomeReconciliationService().check_reported_outcome(
        side="LONG",
        entry="0.21000",
        exit_price="0.19500",
        reported_pnl_pct="0.95",
    )
    assert report.status == "MISMATCH"
    assert report.derived_pnl_pct == Decimal("-7.142857142857142857142857143")
    assert "FINANCIAL_CONSISTENCY_REVIEW" in report.warnings


def test_short_source_result_matches_price_move():
    report = HistoricalOutcomeReconciliationService().check_reported_outcome(
        side="SHORT",
        entry="68655.60",
        exit_price="68400.00",
        reported_pnl_pct="0.3723",
        tolerance_pct="0.01",
    )
    assert report.status == "MATCH"
    assert report.derived_pnl_pct == Decimal("0.3722930103298201457710660165")


def test_timeline_requires_activation_before_close():
    report = HistoricalOutcomeReconciliationService().reconcile_timeline(
        [
            TimelineEventInput("CLOSE", BASE),
            TimelineEventInput("ACTIVATED", BASE + timedelta(minutes=1)),
        ]
    )
    assert report.is_consistent is False
    assert "CLOSE_BEFORE_ACTIVATION" in report.errors


def test_timeline_accepts_activation_targets_and_close_in_order():
    report = HistoricalOutcomeReconciliationService().reconcile_timeline(
        [
            TimelineEventInput("ACTIVATED", BASE),
            TimelineEventInput("TP1", BASE + timedelta(minutes=1)),
            TimelineEventInput("CLOSE", BASE + timedelta(minutes=2)),
        ]
    )
    assert report.is_consistent is True
    assert report.ordered_event_types == ("ACTIVATED", "TP1", "CLOSE")


def test_timeline_flags_target_after_close_and_duplicate_event():
    report = HistoricalOutcomeReconciliationService().reconcile_timeline(
        [
            TimelineEventInput("ACTIVATED", BASE, source_message_id=1),
            TimelineEventInput("CLOSE", BASE + timedelta(minutes=2), source_message_id=2),
            TimelineEventInput("TP1", BASE + timedelta(minutes=3), source_message_id=3),
            TimelineEventInput("TP1", BASE + timedelta(minutes=3), source_message_id=3),
        ]
    )
    assert report.is_consistent is False
    assert "TP1_AFTER_CLOSE" in report.errors
    assert "DUPLICATE_EVENT:TP1:3" in report.errors
