import asyncio
from types import SimpleNamespace

from capitalguard.interfaces.telegram.historical_forwarding_handler import (
    AUTO_BATCH_KEY,
    BATCH_KEY,
    historical_forwarding_active,
)
from capitalguard.interfaces.telegram.forward_parsing_handler import suppress_forwarded_live_fallback


def test_historical_forwarding_active_only_when_batch_is_staged():
    context = SimpleNamespace(user_data={})
    assert historical_forwarding_active(context) is False

    context.user_data[BATCH_KEY] = 42
    assert historical_forwarding_active(context) is True

    context.user_data[BATCH_KEY] = None
    assert historical_forwarding_active(context) is False

    context.user_data[AUTO_BATCH_KEY] = 99
    assert historical_forwarding_active(context) is True


def test_stale_live_parser_suppresses_forward_without_cancel_message():
    context = SimpleNamespace(user_data={"parsing_attempt_id": 12, "raw_forwarded_text": "old"})
    result = asyncio.run(suppress_forwarded_live_fallback(SimpleNamespace(), context))
    assert result == -1
    assert context.user_data == {}
