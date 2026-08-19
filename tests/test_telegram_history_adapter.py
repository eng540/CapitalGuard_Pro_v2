from capitalguard.application.services.telegram_history_adapter import TelegramExportAdapter


def test_telegram_export_adapter_builds_manifest_from_export_payload():
    payload = {
        "messages": [
            {
                "id": 10,
                "type": "message",
                "date": "2026-01-01T12:00:00+00:00",
                "text": ["#BTCUSDT ", {"type": "bold", "text": "LONG"}],
                "from": "Analyst",
            },
            {"id": 11, "type": "service", "date": "2026-01-01T12:01:00+00:00", "text": "joined"},
            {"id": 12, "type": "message", "date": "2026-01-01T12:02:00+00:00", "text": ""},
        ]
    }

    manifest = TelegramExportAdapter().to_manifest(
        payload,
        telegram_channel_id=-100123,
        source_uri="file:///tmp/export.json",
    )

    assert manifest["source_kind"] == "TELEGRAM_EXPORT"
    assert len(manifest["records"]) == 1
    assert manifest["records"][0]["telegram_message_id"] == 10
    assert manifest["records"][0]["raw_text"] == "#BTCUSDT LONG"
    assert manifest["records"][0]["metadata"]["from"] == "Analyst"
