from capitalguard.config import Settings


def test_default_commercial_controls_remain_disabled():
    settings = Settings(BILLING_ENABLED=False, COPY_TRADING_ENABLED=False)

    assert settings.BILLING_ENABLED is False
    assert settings.COPY_TRADING_ENABLED is False
