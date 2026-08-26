import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_service"))

from main import ImageParseRequest, parse_trade_image  # noqa: E402
import main as ai_main  # noqa: E402


@pytest.mark.asyncio
async def test_image_provider_rate_limit_is_exposed_as_503(monkeypatch):
    class FakeManager:
        def __init__(self, **_kwargs):
            pass

        async def analyze_image(self):
            return {
                "status": "error",
                "error_code": "provider_rate_limited",
                "error": "AI provider is temporarily rate-limited; retry later.",
                "parser_path_used": "provider_rate_limited",
            }

    monkeypatch.setattr(ai_main, "ParsingManager", FakeManager)

    with pytest.raises(HTTPException) as caught:
        await parse_trade_image(
            ImageParseRequest(user_id=1, image_url="https://example.com/signal.png")
        )

    assert caught.value.status_code == 503
    assert caught.value.headers["Retry-After"] == "5"


@pytest.mark.asyncio
async def test_image_provider_unavailable_is_exposed_as_503(monkeypatch):
    class FakeManager:
        def __init__(self, **_kwargs):
            pass

        async def analyze_image(self):
            return {
                "status": "error",
                "error_code": "provider_unavailable",
                "error": "AI provider routes are unavailable; retry later.",
                "parser_path_used": "provider_unavailable",
            }

    monkeypatch.setattr(ai_main, "ParsingManager", FakeManager)

    with pytest.raises(HTTPException) as caught:
        await parse_trade_image(
            ImageParseRequest(user_id=1, image_url="https://example.com/signal.png")
        )

    assert caught.value.status_code == 503
