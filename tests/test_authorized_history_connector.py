from datetime import datetime, timezone

import pytest

from capitalguard.application.services.authorized_history_connector import (
    AuthorizedHistoryConnector,
    ConnectorAccessError,
    ConnectorMessage,
    ReaderAccountPolicy,
)


class FakeHistoryBackend:
    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    def get_history(self, *, channel_id, from_message_id, limit):
        self.calls.append((channel_id, from_message_id, limit))
        eligible = [item for item in self.messages if item.channel_id == channel_id and item.message_id < from_message_id]
        if from_message_id == 0:
            eligible = [item for item in self.messages if item.channel_id == channel_id]
        return sorted(eligible, key=lambda item: item.message_id, reverse=True)[:limit]


def _message(message_id, channel_id=-1001):
    return ConnectorMessage(
        channel_id=channel_id,
        message_id=message_id,
        message_timestamp=datetime(2026, 1, 1, 12, message_id, tzinfo=timezone.utc),
        raw_text=f"#BTCUSDT LONG Entry {message_id}",
    )


def test_connector_fetches_allowlisted_history_and_returns_checkpoint():
    backend = FakeHistoryBackend([_message(3), _message(2), _message(1)])
    connector = AuthorizedHistoryConnector(
        policy=ReaderAccountPolicy("system-reader", "railway://telegram/session", frozenset({-1001})),
        backend=backend,
        page_size=2,
    )

    payload, checkpoint = connector.fetch_manifest_page(channel_id=-1001, max_pages=2)

    assert payload["source_kind"] == "AUTHORIZED_USER_HISTORY"
    assert [record["telegram_message_id"] for record in payload["records"]] == [3, 2, 1]
    assert checkpoint.pages_fetched == 2
    assert checkpoint.messages_fetched == 3
    fingerprint = payload["records"][0]["metadata"]["reader_account_fingerprint"]
    assert len(fingerprint) == 16
    assert fingerprint == connector.policy.account_fingerprint


def test_connector_rejects_non_allowlisted_channel_and_disabled_reader():
    backend = FakeHistoryBackend([_message(1)])
    connector = AuthorizedHistoryConnector(
        policy=ReaderAccountPolicy("reader", "secret://session", frozenset({-1001}), enabled=False),
        backend=backend,
    )

    with pytest.raises(ConnectorAccessError, match="disabled"):
        connector.fetch_manifest_page(channel_id=-1001)

    enabled_connector = AuthorizedHistoryConnector(
        policy=ReaderAccountPolicy("reader", "secret://session", frozenset({-1001})),
        backend=backend,
    )
    with pytest.raises(ConnectorAccessError, match="allow-listed"):
        enabled_connector.fetch_manifest_page(channel_id=-2002)


def test_connector_rejects_backend_channel_mismatch():
    class WrongChannelBackend:
        def get_history(self, *, channel_id, from_message_id, limit):
            return [_message(1, channel_id=-2002)]

    backend = WrongChannelBackend()
    connector = AuthorizedHistoryConnector(
        policy=ReaderAccountPolicy("reader", "secret://session", frozenset({-1001})),
        backend=backend,
    )

    with pytest.raises(ConnectorAccessError, match="unexpected channel"):
        connector.fetch_manifest_page(channel_id=-1001)


def test_authorized_page_flows_through_historical_import_dry_run():
    from capitalguard.application.services.historical_import_service import HistoricalImportService

    backend = FakeHistoryBackend([_message(2), _message(1)])
    connector = AuthorizedHistoryConnector(
        policy=ReaderAccountPolicy("reader", "secret://session", frozenset({-1001})),
        backend=backend,
        page_size=10,
    )

    report, checkpoint, payload = HistoricalImportService().dry_run_authorized_page(
        connector,
        channel_id=-1001,
        max_pages=1,
    )

    assert report.is_valid
    assert report.source_kind == "AUTHORIZED_USER_HISTORY"
    assert report.accepted_records == 2
    assert checkpoint.messages_fetched == 2
    assert len(payload["records"]) == 2
