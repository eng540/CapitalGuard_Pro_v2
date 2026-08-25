# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/services/llm_parser.py ---
# File: ai_service/services/llm_parser.py
# Version: v5.3.0 (Smarter Prompt)
# ✅ THE FIX: Updated System Prompt to handle "Performance Cards" and synonyms.

import os
import json
import logging
from typing import Optional, Dict, Any
from services.parsing_utils import (
    normalize_targets, _financial_consistency_check,
    _model_family, _headers_for_call, _post_with_retries,
    configured_fallback_models, _safe_outer_json_extract, _extract_google_response, _extract_openai_response
)
from services.provider_router import get_provider_router, extract_text_response, router_enabled

log = logging.getLogger(__name__)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "google").strip().lower()
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_API_URL = os.getenv("LLM_API_URL")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-1.5-flash").strip()

# ✅ UPDATED PROMPT: Explicitly handles synonyms and performance reports
SYSTEM_PROMPT_TEXT = """
You are an expert crypto trade parser. Your job is to extract trade parameters from text.
The text might be a new signal, OR a forwarded "Performance Card" / "Closed Trade" report.
In ALL cases, extract the ORIGINAL trade setup parameters.

Mapping Rules:
- Side: "BUY", "LONG", "🟢", "📈" -> "LONG". "SELL", "SHORT", "🔴", "📉" -> "SHORT".
- Entry: If multiple entries or a range, pick the first one.
- Targets: Extract all take-profit prices.
- SL: Extract Stop Loss.

Output Format (Strict JSON):
{
  "asset": "BTCUSDT",
  "side": "LONG",      // Must be LONG or SHORT
  "entry": 50000.0,    // Number only
  "stop_loss": 49000.0,// Number only
  "targets": [51000, 52000] // Array of numbers
}

If the text contains "Closed at" or "PnL", IGNORE the exit price and PnL. Extract the original Entry and Stop Loss.
If crucial data (Entry, SL, Asset) is missing, return {"error": "Missing data"}.
"""

def _build_google_payload(text):
    return {
        "contents": [{"parts": [{"text": SYSTEM_PROMPT_TEXT + "\n\nInput Text:\n" + text}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }

def _build_openai_payload(text, model=None):
    return {
        "model": model or LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_TEXT},
            {"role": "user", "content": text}
        ],
        "response_format": {"type": "json_object"}
    }

def _build_openrouter_payload(text):
    """Build an OpenRouter request with explicitly configured model fallbacks."""
    payload = _build_openai_payload(text)
    fallbacks = configured_fallback_models()
    if fallbacks:
        payload["models"] = [LLM_MODEL, *fallbacks]
    return payload

def _normalize_llm_output(raw: str) -> Optional[Dict[str, Any]]:
    try:
        json_str = _safe_outer_json_extract(raw)
        if not json_str:
            return None
        data = json.loads(json_str)
        if data.get("error"):
            return None
        data["targets"] = normalize_targets(data.get("targets"))
        return data if _financial_consistency_check(data) else None
    except Exception as exc:
        log.warning("LLM output normalization failed: %s", type(exc).__name__)
        return None


async def _parse_with_configured_routes(text: str) -> Optional[Dict[str, Any]]:
    router = get_provider_router()
    rate_limited = False
    routes = router.routes_for("text")
    if not routes:
        return {"__error_code__": "provider_unavailable"}
    for route in routes:
        if route.protocol == "fal":
            payload = {"prompt": SYSTEM_PROMPT_TEXT + "\\n\\nInput Text:\\n" + text}
        else:
            payload = _build_openai_payload(text, model=route.model)
        success, body, status, _ = await _post_with_retries(route.api_url, route.headers(), payload)
        if success:
            parsed = _normalize_llm_output(extract_text_response(body))
            if parsed:
                router.record_success(route)
                return parsed
            router.record_failure(route, 200)
        else:
            router.record_failure(route, status)
            rate_limited = rate_limited or status == 429
    if rate_limited:
        return {"__error_code__": "provider_rate_limited"}
    return {"__error_code__": "provider_unavailable"}


async def parse_with_llm(text: str) -> Optional[Dict[str, Any]]:
    if router_enabled():
        routed_result = await _parse_with_configured_routes(text)
        if routed_result is not None:
            return routed_result

    if not LLM_API_KEY or not LLM_API_URL or not LLM_MODEL:
        log.warning("Text LLM configuration is incomplete; skipping LLM parse.")
        return None

    family = _model_family(LLM_MODEL)
    if LLM_PROVIDER == "openrouter":
        # Ox Alpha does not guarantee strict JSON-schema enforcement, so keep
        # the existing JSON-object contract and downstream validation.
        headers = _headers_for_call("openrouter_bearer", LLM_API_KEY)
        payload = _build_openrouter_payload(text)
    elif family == "google":
        headers = _headers_for_call("google_direct", LLM_API_KEY)
        payload = _build_google_payload(text)
    else:
        headers = _headers_for_call("openai_direct", LLM_API_KEY)
        payload = _build_openai_payload(text)

    success, resp_json, status, _ = await _post_with_retries(LLM_API_URL, headers, payload)
    
    if not success:
        if status == 429:
            return {"__error_code__": "provider_rate_limited"}
        return None
    
    try:
        if family == "google": raw = _extract_google_response(resp_json)
        else: raw = _extract_openai_response(resp_json)
        
        json_str = _safe_outer_json_extract(raw)
        if not json_str: return None
        
        data = json.loads(json_str)
        if data.get("error"): return None
        
        # Normalize
        data["targets"] = normalize_targets(data.get("targets"))
        
        # Validation (Uses the new robust check in parsing_utils)
        if _financial_consistency_check(data):
            return data
            
    except Exception as e:
        log.error(f"LLM Parse Error: {e}")
        
    return None
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---