from datetime import datetime, timezone

import pytest

from capitalguard.application.services.authorized_history_connector import (
    AuthorizedHistoryConnector,
    ReaderAccountPolicy,
)
from capitalguard.application.services.telethon_history_backend import TelethonHistoryBackend


class FakeTelethonMessage:
    def __init__(self, message_id):
        self.id = message_id
        self.date = datetime(2026, 1, 1, 12, message_id, tzinfo=timezone.utc)
        self.edit_date = None
        self.message = f"#BTCUSDT LONG Entry {message_id}"
        self.reply_to_msg_id = None


class FakeTelethonClient:
    def __init__(self):
        self.calls = []

    async def iter_messages(self, **kwargs):
        self.calls.append(kwargs)
        for message_id in (3, 2, 1)[: kwargs["limit"]]:
            yield FakeTelethonMessage(message_id)


@pytest.mark.asyncio
async def test_telethon_backend_maps_messages_and_async_connector():
    client = FakeTelethonClient()
    connector = AuthorizedHistoryConnector(
        policy=ReaderAccountPolicy("reader", "secret://session", frozenset({-1001})),
        backend=TelethonHistoryBackend(client),
        page_size=2,
    )

    payload, checkpoint = await connector.fetch_manifest_page_async(channel_id=-1001, max_pages=1)

    assert payload["source_kind"] == "AUTHORIZED_USER_HISTORY"
    assert [record["telegram_message_id"] for record in payload["records"]] == [3, 2]
    assert checkpoint.pages_fetched == 1
    assert client.calls[0]["entity"] == -1001
    assert client.calls[0]["limit"] == 2
