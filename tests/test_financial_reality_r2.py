from decimal import Decimal

from capitalguard.domain.financial_metrics import price_return_pct


def test_long_100_to_110_is_plus_10_percent():
    assert price_return_pct(Decimal("100"), Decimal("110"), "LONG") == Decimal("10")


def test_long_100_to_90_is_minus_10_percent():
    assert price_return_pct(Decimal("100"), Decimal("90"), "LONG") == Decimal("-10")


def test_short_100_to_90_is_plus_10_percent():
    assert price_return_pct(Decimal("100"), Decimal("90"), "SHORT") == Decimal("10")


def test_short_100_to_110_is_minus_10_percent():
    assert price_return_pct(Decimal("100"), Decimal("110"), "SHORT") == Decimal("-10")
