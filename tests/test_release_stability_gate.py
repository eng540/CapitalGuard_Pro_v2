from capitalguard.application.services.release_stability_gate_service import (
    ReleaseStabilityGateService,
    StabilityGateInput,
)


def test_release_gate_passes_only_clean_noncommercial_snapshot():
    report = ReleaseStabilityGateService().evaluate(
        StabilityGateInput(
            tests_passed=10,
            tests_failed=0,
            skipped_tests=0,
            outbox_queue_size=0,
            live_entity_leaks=0,
            unreviewed_financial_conflicts=0,
            replay_pending_records=0,
        )
    )

    assert report.status == "PASS"
    assert report.commercial_enabled is False
    assert report.copy_trading_enabled is False


def test_release_gate_holds_when_historical_replay_is_pending():
    report = ReleaseStabilityGateService().evaluate(
        StabilityGateInput(
            tests_passed=10,
            tests_failed=0,
            skipped_tests=0,
            outbox_queue_size=0,
            live_entity_leaks=0,
            unreviewed_financial_conflicts=0,
            replay_pending_records=1,
        )
    )

    assert report.status == "HOLD"
    assert "REPLAY_PENDING_BACKLOG" in report.reasons
    assert report.commercial_enabled is False
    assert report.copy_trading_enabled is False
