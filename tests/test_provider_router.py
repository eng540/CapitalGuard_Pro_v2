import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_service"))

from services.provider_router import ProviderRouter, extract_text_response


def test_router_loads_routes_from_json_using_secret_env_names(monkeypatch):
    monkeypatch.setenv("ROUTER_OPENROUTER_KEY", "router-key")
    monkeypatch.setenv(
        "AI_MODEL_ROUTES",
        json.dumps([
            {
                "name": "primary",
                "provider": "openrouter",
                "model": "stealth/ox-alpha",
                "api_url": "https://openrouter.ai/api/v1/chat/completions",
                "api_key_env": "ROUTER_OPENROUTER_KEY",
                "protocol": "openai",
                "capabilities": ["text", "vision"],
                "priority": 10,
            },
            {
                "name": "vision-fallback",
                "provider": "huggingface",
                "model": "zai-org/GLM-4.5V",
                "api_url": "https://router.huggingface.co/v1/chat/completions",
                "api_key_env": "HUGGINGFACE_TOKEN",
                "protocol": "openai",
                "capabilities": ["vision"],
                "priority": 20,
            },
        ]),
    )
    monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf-key")

    router = ProviderRouter.from_env()
    routes = router.routes_for("vision")

    assert [route.route_name for route in routes] == ["primary", "vision-fallback"]
    assert routes[0].headers()["Authorization"] == "Bearer router-key"
    assert routes[1].headers()["Authorization"] == "Bearer hf-key"


def test_router_circuit_breaker_temporarily_removes_unhealthy_route(monkeypatch):
    monkeypatch.setenv("ROUTER_KEY", "key")
    monkeypatch.setenv("AI_MODEL_ROUTES", json.dumps([{
        "name": "route",
        "provider": "openrouter",
        "model": "model-a",
        "api_url": "https://openrouter.ai/api/v1/chat/completions",
        "api_key_env": "ROUTER_KEY",
        "capabilities": ["text"],
        "priority": 1,
    }]))
    router = ProviderRouter.from_env()
    route = router.routes_for("text")[0]

    for _ in range(3):
        router.record_failure(route, 503)

    assert router.routes_for("text") == []
    assert router.public_status()[0]["circuit_open"] is True


def test_extract_text_response_supports_openai_and_fal_envelopes():
    assert extract_text_response({"choices": [{"message": {"content": '{"asset":"BTCUSDT"}'}}]})
    assert extract_text_response({"output": {"text": "fal output"}}) == "fal output"


import pytest


@pytest.mark.asyncio
async def test_text_router_fails_over_from_openrouter_to_huggingface(monkeypatch):
    monkeypatch.setenv("AI_ROUTER_ENABLED", "1")
    monkeypatch.setenv("ROUTER_KEY", "primary-key")
    monkeypatch.setenv("HF_KEY", "fallback-key")
    monkeypatch.setenv(
        "AI_MODEL_ROUTES",
        json.dumps([
            {
                "name": "openrouter-primary",
                "provider": "openrouter",
                "model": "stealth/ox-alpha",
                "api_url": "https://openrouter.ai/api/v1/chat/completions",
                "api_key_env": "ROUTER_KEY",
                "capabilities": ["text"],
                "priority": 10,
            },
            {
                "name": "hf-fallback",
                "provider": "huggingface",
                "model": "zai-org/GLM-4.5V",
                "api_url": "https://router.huggingface.co/v1/chat/completions",
                "api_key_env": "HF_KEY",
                "capabilities": ["text"],
                "priority": 20,
            },
        ]),
    )

    from services import llm_parser

    attempts = []

    async def fake_post(url, headers, payload):
        attempts.append((url, headers, payload))
        if "openrouter.ai" in url:
            return False, {"error": "rate limited"}, 429, "rate limited"
        return True, {
            "choices": [{"message": {"content": json.dumps({
                "asset": "BTCUSDT",
                "side": "LONG",
                "entry": 100,
                "stop_loss": 95,
                "targets": [{"price": 110, "close_percent": 100}],
            })}}],
        }, 200, "ok"

    monkeypatch.setattr(llm_parser, "_post_with_retries", fake_post)
    result = await llm_parser.parse_with_llm("#BTC LONG entry 100 stop 95 target 110")

    assert result["asset"] == "BTCUSDT"
    assert [attempt[0] for attempt in attempts] == [
        "https://openrouter.ai/api/v1/chat/completions",
        "https://router.huggingface.co/v1/chat/completions",
    ]
    assert attempts[1][2]["model"] == "zai-org/GLM-4.5V"


def test_legacy_provider_order_builds_fal_endpoint_and_huggingface_route(monkeypatch):
    monkeypatch.delenv("AI_MODEL_ROUTES", raising=False)
    monkeypatch.setenv("AI_PROVIDER_ORDER", "fal,huggingface")
    monkeypatch.setenv("FAL_KEY", "fal-key")
    monkeypatch.setenv("FAL_MODEL", "fal-ai/vision-model")
    monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf-key")
    monkeypatch.setenv("HUGGINGFACE_MODEL", "zai-org/GLM-4.5V")

    routes = ProviderRouter.from_env().routes

    assert routes[0].api_url == "https://fal.run/fal-ai/vision-model"
    assert routes[0].protocol == "fal"
    assert routes[0].headers()["Authorization"] == "Key fal-key"
    assert routes[1].api_url == "https://router.huggingface.co/v1/chat/completions"
    assert routes[1].headers()["Authorization"] == "Bearer hf-key"


def test_json_router_rejects_custom_urls_by_default(monkeypatch):
    monkeypatch.setenv("ROUTER_KEY", "key")
    monkeypatch.setenv("AI_MODEL_ROUTES", json.dumps([{
        "provider": "openrouter",
        "model": "model-a",
        "api_url": "http://internal-service:8000/parse",
        "api_key_env": "ROUTER_KEY",
        "capabilities": ["text"],
    }]))
    assert ProviderRouter.from_env().routes == ()



def test_router_permanently_isolates_credential_and_route_failures(monkeypatch):
    monkeypatch.setenv("ROUTER_KEY", "key")
    monkeypatch.setenv("AI_MODEL_ROUTES", json.dumps([{
        "name": "route",
        "provider": "openrouter",
        "model": "model-a",
        "api_url": "https://openrouter.ai/api/v1/chat/completions",
        "api_key_env": "ROUTER_KEY",
        "capabilities": ["vision"],
        "priority": 1,
    }]))
    monkeypatch.setenv("AI_CIRCUIT_PERMANENT_COOLDOWN_SECONDS", "900")

    router = ProviderRouter.from_env()
    route = router.routes_for("vision")[0]
    router.record_failure(route, 403)

    assert router.routes_for("vision") == []
    assert router.public_status()[0]["circuit_open"] is True


# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
