from datetime import datetime, timedelta, timezone

import pytest

from capitalguard.config import Settings, get_r5_observation_status, settings, validate_r5_noncommercial_controls


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


def test_r5_observation_status_reports_missing_and_elapsed_window(monkeypatch):
    monkeypatch.setattr(settings, "R5_OBSERVATION_STARTED_AT", None)
    monkeypatch.setattr(settings, "R5_OBSERVATION_WINDOW_HOURS", 24)
    assert get_r5_observation_status()["complete"] is False

    started = datetime(2026, 8, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(settings, "R5_OBSERVATION_STARTED_AT", started)
    status = get_r5_observation_status(now=started + timedelta(hours=25))
    assert status["elapsed_hours"] == 25
    assert status["remaining_hours"] == 0
    assert status["complete"] is True
