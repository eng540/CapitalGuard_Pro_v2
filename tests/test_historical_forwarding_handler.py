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


def test_forwarded_input_preserves_photo_identity_for_historical_processing():
    from datetime import datetime, timezone
    from capitalguard.interfaces.telegram.historical_forwarding_handler import _forwarded_input

    message = SimpleNamespace(
        chat_id=500,
        message_id=700,
        text=None,
        caption="BTC LONG",
        date=datetime(2026, 8, 20, tzinfo=timezone.utc),
        edit_date=None,
        reply_to_message=None,
        photo=[SimpleNamespace(file_id="small", file_unique_id="small-u", width=100, height=100), SimpleNamespace(file_id="large", file_unique_id="large-u", width=1000, height=1000)],
    )
    origin_chat = SimpleNamespace(id=-100123, title="Test", username="test")
    origin = SimpleNamespace(chat=origin_chat, date=datetime(2026, 8, 19, tzinfo=timezone.utc), message_id=77, author_signature=None)
    details = {
        "origin": origin,
        "origin_type": "CHANNEL",
        "source_timestamp": origin.date,
        "source_message_id": origin.message_id,
        "source_chat_id": origin_chat.id,
        "source_title": origin_chat.title,
        "source_username": origin_chat.username,
    }

    item = _forwarded_input(message, user_id=99, details=details)

    assert item.raw_text == "BTC LONG"
    assert item.metadata["media"]["file_id"] == "large"
    assert item.metadata["media"]["media_unique_id"] == "large-u"
