import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "ai_service"))

from ai_service.services import image_parser, llm_parser, parsing_utils


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


@pytest.mark.asyncio
async def test_ox_alpha_429_is_classified_as_provider_rate_limited(monkeypatch):
    async def fake_post(*_args, **_kwargs):
        return False, {"error": "rate limited"}, 429, "provider returned 429"

    monkeypatch.setattr(llm_parser, "LLM_PROVIDER", "openrouter")
    monkeypatch.setattr(llm_parser, "LLM_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(llm_parser, "LLM_API_URL", OPENROUTER_URL)
    monkeypatch.setattr(llm_parser, "LLM_MODEL", OX_ALPHA_MODEL)
    monkeypatch.setattr(llm_parser, "_post_with_retries", fake_post)

    result = await llm_parser.parse_with_llm("BTCUSDT LONG entry 70000 SL 69000 TP 71000")

    assert result == {"__error_code__": "provider_rate_limited"}


@pytest.mark.asyncio
async def test_post_with_retries_is_bounded_for_429(monkeypatch):
    calls = 0

    class _RateLimitedResponse:
        status_code = 429
        text = '{"error":"rate limited"}'

        def json(self):
            return {"error": "rate limited"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            return _RateLimitedResponse()

    async def fake_sleep(_delay):
        return None

    monkeypatch.setenv("LLM_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("LLM_BACKOFF_BASE", "0.01")
    monkeypatch.setattr(parsing_utils.httpx, "AsyncClient", lambda: _Client())
    monkeypatch.setattr(parsing_utils.asyncio, "sleep", fake_sleep)

    success, body, status, text = await parsing_utils._post_with_retries(
        OPENROUTER_URL, {"Authorization": "Bearer test-key"}, {"model": OX_ALPHA_MODEL}
    )

    assert success is False
    assert body == {"error": "rate limited"}
    assert status == 429
    assert calls == 2
    assert "rate limited" in text


def test_telegram_bot_token_is_redacted_from_logs():
    secret_url = "https://api.telegram.org/file/bot7694868673:AASecretToken/photos/file_28.jpg"

    redacted = parsing_utils.redact_sensitive_url(secret_url)

    assert "AASecretToken" not in redacted
    assert "<redacted>" in redacted
    assert "api.telegram.org" in redacted


def test_openrouter_payload_includes_configured_model_fallbacks(monkeypatch):
    monkeypatch.setattr(llm_parser, "LLM_MODEL", OX_ALPHA_MODEL)
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "google/gemini-3.1-flash-lite, openai/gpt-4o-mini")

    payload = llm_parser._build_openrouter_payload("BTCUSDT LONG entry 70000 SL 69000 TP 71000")

    assert payload["models"] == [OX_ALPHA_MODEL, "google/gemini-3.1-flash-lite", "openai/gpt-4o-mini"]


def test_fallback_models_are_limited_to_three(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "a,b,c,d")

    assert parsing_utils.configured_fallback_models() == ["a", "b", "c"]
