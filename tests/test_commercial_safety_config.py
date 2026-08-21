import pytest

from capitalguard.config import Settings, settings, validate_r5_noncommercial_controls


def test_default_commercial_controls_remain_disabled():
    configured = Settings(BILLING_ENABLED=False, COPY_TRADING_ENABLED=False, AUTO_TRADE_ENABLED=False, TRADE_LIVE_ENABLED=False)

    assert configured.BILLING_ENABLED is False
    assert configured.COPY_TRADING_ENABLED is False
    assert configured.AUTO_TRADE_ENABLED is False
    assert configured.TRADE_LIVE_ENABLED is False


@pytest.mark.parametrize("flag", ["BILLING_ENABLED", "COPY_TRADING_ENABLED", "AUTO_TRADE_ENABLED", "TRADE_LIVE_ENABLED"])
def test_r5_gate_fails_closed_when_any_commercial_or_execution_control_is_enabled(monkeypatch, flag):
    monkeypatch.setattr(settings, flag, True)

    with pytest.raises(RuntimeError, match="R5 noncommercial gate rejected"):
        validate_r5_noncommercial_controls()
