import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "ai_service"))

from ai_service.services import image_parser, llm_parser


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OX_ALPHA_MODEL = "stealth/ox-alpha"


def _completion_response(payload):
    return {
        "id": "test-generation",
        "created": 1787256295,
        "model": OX_ALPHA_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(payload)},
                "finish_reason": "stop",
            }
        ],
    }


@pytest.mark.asyncio
async def test_ox_alpha_text_uses_openrouter_contract_and_preserves_normalization(monkeypatch):
    calls = []

    async def fake_post(url, headers, payload):
        calls.append((url, headers, payload))
        return True, _completion_response(
            {
                "asset": "BTCUSDT",
                "side": "LONG",
                "entry": "70000",
                "stop_loss": "69000",
                "targets": ["71000", "72000"],
            }
        ), 200, "ok"

    monkeypatch.setattr(llm_parser, "LLM_PROVIDER", "openrouter")
    monkeypatch.setattr(llm_parser, "LLM_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(llm_parser, "LLM_API_URL", OPENROUTER_URL)
    monkeypatch.setattr(llm_parser, "LLM_MODEL", OX_ALPHA_MODEL)
    monkeypatch.setattr(llm_parser, "_post_with_retries", fake_post)

    result = await llm_parser.parse_with_llm("BTCUSDT LONG entry 70000 SL 69000 TP 71000 72000")

    assert result is not None
    assert result["asset"] == "BTCUSDT"
    assert str(result["entry"]) == "70000"
    assert str(result["stop_loss"]) == "69000"
    assert [str(target["price"]) for target in result["targets"]] == ["71000", "72000"]
    assert result["targets"][-1]["close_percent"] == 100.0

    assert len(calls) == 1
    url, headers, payload = calls[0]
    assert url == OPENROUTER_URL
    assert headers["Authorization"] == "Bearer test-openrouter-key"
    assert payload["model"] == OX_ALPHA_MODEL
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"


@pytest.mark.asyncio
async def test_ox_alpha_text_rejects_financially_inconsistent_output(monkeypatch):
    async def fake_post(*_args, **_kwargs):
        return True, _completion_response(
            {
                "asset": "BTCUSDT",
                "side": "LONG",
                "entry": "70000",
                "stop_loss": "71000",
                "targets": ["72000"],
            }
        ), 200, "ok"

    monkeypatch.setattr(llm_parser, "LLM_PROVIDER", "openrouter")
    monkeypatch.setattr(llm_parser, "LLM_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(llm_parser, "LLM_API_URL", OPENROUTER_URL)
    monkeypatch.setattr(llm_parser, "LLM_MODEL", OX_ALPHA_MODEL)
    monkeypatch.setattr(llm_parser, "_post_with_retries", fake_post)

    result = await llm_parser.parse_with_llm("invalid LONG signal")

    assert result is None


class _ImageResponse:
    content = b"fake-png-bytes"
    headers = {"content-type": "image/png"}

    def raise_for_status(self):
        return None


class _ImageClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *_args, **_kwargs):
        return _ImageResponse()


@pytest.mark.asyncio
async def test_ox_alpha_vision_uses_existing_openrouter_branch(monkeypatch):
    calls = []

    async def fake_post(url, headers, payload):
        calls.append((url, headers, payload))
        return True, _completion_response(
            {
                "asset": "BTCUSDT",
                "market": "Futures",
                "side": "LONG",
                "entry": "70000",
                "stop_loss": "69000",
                "targets": [{"price": "71000", "close_percent": 100.0}],
            }
        ), 200, "ok"

    monkeypatch.setattr(image_parser, "LLM_PROVIDER", "openrouter")
    monkeypatch.setattr(image_parser, "LLM_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(image_parser, "LLM_API_URL", OPENROUTER_URL)
    monkeypatch.setattr(image_parser, "LLM_MODEL", OX_ALPHA_MODEL)
    monkeypatch.setattr(image_parser, "_post_with_retries", fake_post)
    monkeypatch.setattr(image_parser.httpx, "AsyncClient", lambda: _ImageClient())

    result = await image_parser.parse_with_vision("https://telegram.test/file/signal.png")

    assert result is not None
    assert result["asset"] == "BTCUSDT"
    assert str(result["entry"]) == "70000"
    assert str(result["stop_loss"]) == "69000"

    assert len(calls) == 1
    url, headers, payload = calls[0]
    assert url == OPENROUTER_URL
    assert headers["Authorization"] == "Bearer test-openrouter-key"
    assert payload["model"] == OX_ALPHA_MODEL
    assert payload["response_format"] == {"type": "json_object"}
    image_parts = payload["messages"][1]["content"]
    assert image_parts[0]["type"] == "image_url"
    assert image_parts[0]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_vision_fails_closed_for_unknown_provider(monkeypatch):
    monkeypatch.setattr(image_parser, "LLM_PROVIDER", "unknown-provider")
    monkeypatch.setattr(image_parser, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(image_parser, "LLM_API_URL", OPENROUTER_URL)
    monkeypatch.setattr(image_parser, "LLM_MODEL", OX_ALPHA_MODEL)
    monkeypatch.setattr(image_parser.httpx, "AsyncClient", lambda: _ImageClient())

    async def should_not_post(*_args, **_kwargs):
        raise AssertionError("unknown provider must not make an upstream request")

    monkeypatch.setattr(image_parser, "_post_with_retries", should_not_post)

    result = await image_parser.parse_with_vision("https://telegram.test/file/signal.png")

    assert result is None
