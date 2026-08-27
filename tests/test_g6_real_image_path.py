import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "ai_service"))

from ai_service.services import image_parser
from capitalguard.application.services.historical_message_foundation_service import (
    HistoricalMessageFoundationService,
)
from capitalguard.application.services.historical_semantic_materialization_service import (
    HistoricalSemanticMaterializationService,
)
from tests.test_historical_evidence_ingestion_service import make_reviewed_batch


class _ImageResponse:
    def __init__(self, content):
        self.content = content
        self.headers = {"content-type": "image/png"}

    def raise_for_status(self):
        return None


class _ImageClient:
    def __init__(self, content):
        self.content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *_args, **_kwargs):
        return _ImageResponse(self.content)


def _make_signal_png(path):
    fixture = Path(__file__).parent / "fixtures" / "g6_btc_signal.png"
    path.write_bytes(fixture.read_bytes())


async def _fake_post_with_retries(*_args, **_kwargs):
    response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "asset": "BTC",
                            "market": "Futures",
                            "side": "LONG",
                            "entry": "77000",
                            "stop_loss": "76000",
                            "targets": [{"price": "78000", "close_percent": 100.0}],
                            "leverage": "5",
                        }
                    )
                }
            }
        ]
    }
    return True, response, 200, json.dumps(response)


@pytest.mark.asyncio
async def test_real_png_traverses_existing_vision_adapter_and_historical_chain(
    tmp_path, monkeypatch, db_session
):
    png_path = tmp_path / "btc_signal.png"
    _make_signal_png(png_path)
    image_bytes = png_path.read_bytes()

    monkeypatch.setattr(image_parser, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(image_parser, "LLM_API_URL", "https://vision.test/v1")
    monkeypatch.setattr(image_parser, "LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(image_parser, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(image_parser, "_post_with_retries", _fake_post_with_retries)
    monkeypatch.setattr(image_parser.httpx, "AsyncClient", lambda: _ImageClient(image_bytes))

    vision_result = await image_parser.parse_with_vision("https://telegram.test/file/btc_signal.png")

    assert vision_result["asset"] == "BTC"
    assert str(vision_result["entry"]) == "77000"
    assert str(vision_result["stop_loss"]) == "76000"
    assert str(vision_result["targets"][0]["price"]) == "78000"
    assert vision_result["leverage"] == "5"

    _, receipt = make_reviewed_batch(db_session)
    receipt.raw_text = "BTC Futures LONG"
    receipt.metadata_json = {
        "media": {
            "media_type": "PHOTO",
            "file_id": "telegram-photo-file",
            "media_unique_id": "telegram-photo-unique",
        }
    }
    receipt.source_message_timestamp = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    revision = HistoricalMessageFoundationService().record_receipt(db_session, receipt=receipt)

    materialized = HistoricalSemanticMaterializationService().materialize_revision(
        db_session,
        revision_id=revision.id,
        image_result={"status": "success", "data": vision_result},
        image_provenance={
            "media_id": "telegram-photo-unique",
            "file_id": "telegram-photo-file",
            "provider": "openai",
            "model": "gpt-4o-mini",
        },
    )

    assert materialized["status"] == "SUCCESS"
    assert materialized["canonical"]["entry"] == "77000"
    assert materialized["canonical"]["stop_loss"] == "76000"
    assert materialized["canonical"]["targets"] == ["78000"]
    assert materialized["canonical"]["leverage"] == "5"
    entry_evidence = materialized["field_evidence"]["entry"][0]
    assert entry_evidence["modality"] == "IMAGE"
    assert entry_evidence["provenance"]["media_id"] == "telegram-photo-unique"
    assert entry_evidence["provenance"]["model"] == "gpt-4o-mini"


class _ProxyResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _ProxyClient:
    def __init__(self, payload, *args, **kwargs):
        self.payload = payload
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, *, params=None, json=None):
        self.calls.append((url, params, json))
        assert url.endswith("/parse_image")
        assert json["image_url"].split("?", 1)[0].endswith("btc_signal.png")
        return _ProxyResponse({"status": "success", "data": self.payload})


@pytest.mark.asyncio
async def test_file_id_proxy_reaches_existing_image_service_and_semantic_chain(db_session, monkeypatch):
    from capitalguard.application.services import image_parsing_service

    payload = {
        "asset": "BTC",
        "market": "Futures",
        "side": "LONG",
        "entry": "77000",
        "stop_loss": "76000",
        "targets": [{"price": "78000", "close_percent": 100.0}],
        "leverage": "5",
    }
    clients = []

    def make_client(*args, **kwargs):
        client = _ProxyClient(payload, *args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(image_parsing_service, "AI_SERVICE_URL", "https://ai.test/ai/parse")
    monkeypatch.setattr(image_parsing_service, "BOT_TOKEN", "test-token")
    monkeypatch.setattr(image_parsing_service.httpx, "AsyncClient", make_client)
    monkeypatch.setattr(
        image_parsing_service.ImageParsingService,
        "_get_telegram_file_url",
        lambda self, file_id: _async_value(f"https://telegram.test/file/btc_signal.png?file_id={file_id}"),
    )

    service = image_parsing_service.ImageParsingService()
    result = await service.parse_image_from_file_id(99, "telegram-photo-file")

    assert result["status"] == "success"
    assert result["data"]["entry"] == "77000"
    assert result["data"]["targets"][0]["price"] == "78000"
    assert clients and clients[0].calls[0][2]["user_id"] == 99

    _, receipt = make_reviewed_batch(db_session)
    receipt.raw_text = "BTC Futures LONG"
    receipt.metadata_json = {
        "media": {
            "media_type": "PHOTO",
            "file_id": "telegram-photo-file",
            "media_unique_id": "telegram-photo-unique",
        }
    }
    revision = HistoricalMessageFoundationService().record_receipt(db_session, receipt=receipt)
    projection = HistoricalSemanticMaterializationService().materialize_revision(
        db_session,
        revision_id=revision.id,
        image_result=result,
        image_provenance={"media_id": "telegram-photo-unique", "file_id": "telegram-photo-file"},
    )

    assert projection["status"] == "SUCCESS"
    assert projection["canonical"]["targets"] == ["78000"]
    assert projection["field_evidence"]["entry"][0]["modality"] == "IMAGE"


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_historical_handler_helper_runs_file_id_to_semantic_materialization(
    db_session, monkeypatch
):
    from types import SimpleNamespace
    from capitalguard.interfaces.telegram.historical_forwarding_handler import (
        _materialize_historical_content,
    )

    _, receipt = make_reviewed_batch(db_session)
    receipt.raw_text = "BTC Futures LONG"
    receipt.metadata_json = {
        "media": {
            "media_type": "PHOTO",
            "file_id": "telegram-photo-file-2",
            "media_unique_id": "telegram-photo-unique-2",
        }
    }
    revision = HistoricalMessageFoundationService().record_receipt(db_session, receipt=receipt)
    payload = {
        "status": "success",
        "data": {
            "asset": "BTC",
            "market": "Futures",
            "side": "LONG",
            "entry": "77000",
            "stop_loss": "76000",
            "targets": [{"price": "78000", "close_percent": 100.0}],
            "leverage": "5",
        },
    }

    async def fake_parse(self, user_id, file_id):
        assert user_id == 99
        assert file_id == "telegram-photo-file-2"
        return payload

    monkeypatch.setattr(
        "capitalguard.interfaces.telegram.historical_forwarding_handler.ImageParsingService.parse_image_from_file_id",
        fake_parse,
    )

    result = await _materialize_historical_content(
        db_session,
        SimpleNamespace(id=99),
        receipt,
    )

    assert result["status"] == "SUCCESS"
    assert result["canonical"]["entry"] == "77000"
    assert result["canonical"]["targets"] == ["78000"]
    assert result["field_evidence"]["entry"][0]["modality"] == "IMAGE"
    assert result["field_evidence"]["entry"][0]["provenance"]["media_id"] == "telegram-photo-unique-2"
    assert revision.id == result["field_evidence"]["asset"][0]["provenance"]["revision_id"]
    assert (receipt.metadata_json or {}).get("historical_preview", {}).get("canonical", {}).get("entry") == "77000"
    assert (receipt.metadata_json or {}).get("semantic_projection", {}).get("status") == "SUCCESS"


@pytest.mark.asyncio
async def test_historical_handler_helper_preserves_text_image_conflict(db_session, monkeypatch):
    from types import SimpleNamespace
    from capitalguard.interfaces.telegram.historical_forwarding_handler import (
        _materialize_historical_content,
    )

    _, receipt = make_reviewed_batch(db_session)
    receipt.raw_text = "BTC Futures LONG Entry 77K SL 76K TP 78K"
    receipt.metadata_json = {
        "media": {
            "media_type": "PHOTO",
            "file_id": "telegram-photo-conflict",
            "media_unique_id": "telegram-photo-conflict-unique",
        }
    }
    image_payload = {
        "status": "success",
        "data": {
            "asset": "BTC",
            "market": "Futures",
            "side": "LONG",
            "entry": "78000",
            "stop_loss": "76000",
            "targets": [{"price": "79000", "close_percent": 100.0}],
            "leverage": "5",
        },
    }

    async def fake_parse(self, user_id, file_id):
        assert user_id == 99
        assert file_id == "telegram-photo-conflict"
        return image_payload

    monkeypatch.setattr(
        "capitalguard.interfaces.telegram.historical_forwarding_handler.ImageParsingService.parse_image_from_file_id",
        fake_parse,
    )

    result = await _materialize_historical_content(
        db_session,
        SimpleNamespace(id=99),
        receipt,
    )

    assert result["status"] == "CONFLICT"
    assert result["canonical"]["entry"] is None
    assert {item["modality"] for item in result["field_evidence"]["entry"]} == {"TEXT", "IMAGE"}
    assert "entry" in result["conflicting_fields"]
