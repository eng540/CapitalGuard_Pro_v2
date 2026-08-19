from decimal import Decimal

from capitalguard.application.services.financial_consistency_service import FinancialConsistencyService


def test_long_consistency_accepts_ordered_targets_and_full_close():
    report = FinancialConsistencyService().check(
        side="LONG",
        entry=100,
        stop_loss=95,
        targets=[{"price": 105, "close_percent": 50}, {"price": 110, "close_percent": 50}],
    )
    assert report.is_consistent
    assert report.errors == ()


def test_short_consistency_accepts_descending_targets():
    report = FinancialConsistencyService().check(
        side="SHORT",
        entry=200,
        stop_loss=210,
        targets=[{"price": Decimal("190"), "close_percent": 50}, {"price": Decimal("180"), "close_percent": 50}],
    )
    assert report.is_consistent


def test_consistency_rejects_wrong_direction_and_over_close():
    report = FinancialConsistencyService().check(
        side="LONG",
        entry=100,
        stop_loss=105,
        targets=[{"price": 99, "close_percent": 70}, {"price": 110, "close_percent": 40}],
    )
    assert not report.is_consistent
    assert "LONG_STOP_MUST_BE_BELOW_ENTRY" in report.errors
    assert "LONG_TARGET_MUST_BE_ABOVE_ENTRY" in report.errors
    assert "TARGET_CLOSE_PERCENT_OVER_100" in report.errors


def test_consistency_warns_when_close_percent_is_incomplete():
    report = FinancialConsistencyService().check(
        side="LONG",
        entry=100,
        stop_loss=95,
        targets=[{"price": 105, "close_percent": 50}],
    )
    assert report.is_consistent
    assert "TARGET_CLOSE_PERCENT_BELOW_100" in report.warnings
