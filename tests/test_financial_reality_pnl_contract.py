from capitalguard.interfaces.telegram.helpers import _pct


def test_long_price_return_is_canonical():
    assert _pct(100, 110, "LONG") == 10.0
    assert _pct(100, 90, "LONG") == -10.0


def test_short_price_return_is_canonical():
    assert _pct(100, 90, "SHORT") == 10.0
    assert _pct(100, 110, "SHORT") == -10.0


def test_short_does_not_use_inverse_exit_denominator():
    assert _pct(100, 90, "SHORT") != 11.11111111111111


def test_invalid_side_is_not_financial_pnl():
    assert _pct(100, 110, "UNKNOWN") == 0.0
