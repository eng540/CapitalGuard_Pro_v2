from types import SimpleNamespace

from capitalguard.interfaces.telegram.historical_forwarding_handler import (
    BATCH_KEY,
    historical_forwarding_active,
)


def test_historical_forwarding_active_only_when_batch_is_staged():
    context = SimpleNamespace(user_data={})
    assert historical_forwarding_active(context) is False

    context.user_data[BATCH_KEY] = 42
    assert historical_forwarding_active(context) is True

    context.user_data[BATCH_KEY] = None
    assert historical_forwarding_active(context) is False
