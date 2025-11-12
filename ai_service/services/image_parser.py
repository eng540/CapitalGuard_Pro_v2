# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/services/image_parser.py ---
# ai_service/services/image_parser.py (v1.0 - ADR-003)
"""
Service for parsing trade signals from images using a Vision model.

This service:
1. Downloads an image from a provided URL.
2. Encodes it to base64.
3. Sends it to a multimodal LLM (like Gemini) with a specific prompt.
4. Re-uses the *exact same* validation and normalization logic from the
   text-based llm_parser to ensure 100% data consistency.
"""

import os
import re
import json
import logging
import httpx
import base64
from typing import Any, Dict, List, Optional
from decimal import Decimal

# --- ✅ CRITICAL: Reuse validation logic from text parsers ---
from services.parsing_utils import (
    parse_decimal_token, 
    normalize_targets
)
from services.llm_parser import (
    _financial_consistency_check,
    _build_google_headers,
    _extract_google_response,
    _build_openai_headers, # In case user switches to gpt-4o
    _extract_openai_response
)

log = logging.getLogger(__name__)
telemetry_log = logging.getLogger("ai_service.telemetry")

# --- Environment-driven LLM/Vision config (Reuses the same vars) ---
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "google").lower()
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_API_URL = os.getenv("LLM_API_URL") # Assumes this is a vision-capable model URL
LLM_MODEL = os.getenv("LLM_MODEL") # Assumes this is a vision-capable model (e.g., gemini-1.5-flash)

if not all([LLM_API_KEY, LLM_API_URL, LLM_MODEL]):
    log.warning("LLM/Vision environment variables incomplete. Image parsing may be skipped.")

# --- ✅ (ADR-003): New prompt specifically for image analysis ---
SYSTEM_PROMPT_VISION = os.getenv("LLM_SYSTEM_PROMPT_VISION") or """
You are an expert financial analyst.
Your task is to extract structured data from an IMAGE of a trade signal.
The image may be a screenshot from another Telegram channel or trading platform.
Analyze the text visible in the image and return ONLY a valid JSON object.

--- 
### CRITICAL VALIDATION RULES ###
1.  **Asset/Side/Entry/SL/Targets:** You *must* find all five fields.
    If any are missing, respond with `{"error": "Missing required fields."}`.
2.  **LONG Validation:** If "side" is "LONG", "stop_loss" *must* be less than "entry".
3.  **SHORT Validation:** If "side" is "SHORT", "stop_loss" *must* be greater than "entry".
4.  **Targets Validation:** All "targets" prices *must* be greater than "entry" for LONGs, and less than "entry" for SHORTs.
5.  **If any validation rule (2, 3, 4) fails, DO NOT return the data.** Instead, respond with `{"error": "Financial validation failed (e.g., SL vs Entry)."}`.
---

### EXTRACTION RULES (FROM IMAGE) ###
1.  Asset: Find the asset (e.g., "#ETH", "BTCUSDT").
2.  Side: "LONG" or "SHORT", "Buy" or "Sell".
3.  Entry: Find "Entry", "مناطق الدخول", "Entry Price".
    If it's a range, use *ONLY THE FIRST* price.
4.  Stop Loss: Find "SL", "ايقاف خسارة", "Stop Loss".
5.  Targets: Find "Targets", "TPs", "الاهداف". Extract *all* numbers that follow.
6.  **Percentages:** Look for "20% each", "كل هدف 25%". If found, apply to all targets.
    If not found, default to 0.0 (the normalizer will handle it).
7.  Notes: Add any extra text (like "Leverage: 10x") to the "notes" field.
8.  Market/Order: Default to "Futures" and "LIMIT".

You will be given an image. Respond ONLY with the JSON object.
"""

# --- Payload Builders ---

def _build_google_vision_payload(image_base64: str, image_mime_type: str) -> Dict[str, Any]:
    """Builds the payload for Google Gemini (multimodal)."""
    return {
        "contents": [
            {
                "parts": [
                    {"text": SYSTEM_PROMPT_VISION},
                    {
                        "inline_data": {
                            "mime_type": image_mime_type,
                            "data": image_base64
                        }
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.0
        }
    }

def _build_openai_vision_payload(image_base64: str, image_mime_type: str) -> Dict[str, Any]:
    """Builds the payload for OpenAI GPT-4o (multimodal)."""
    return {
        "model": LLM_MODEL,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT_VISION
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{image_mime_type};base64,{image_base64}"
                        }
                    }
                ]
            }
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 2048
    }

# --- Main Parse Function ---

async def parse_with_vision(image_url: str) -> Optional[Dict[str, Any]]:
    """
    Downloads, encodes, and calls the Vision provider to parse a trade from an image.
    Returns normalized dict or None on failure.
    """
    if not all([LLM_API_KEY, LLM_API_URL, LLM_MODEL]):
        log.debug("Vision config incomplete; skipping vision parse.")
        return None

    log_meta = {"event": "vision_parse", "provider": LLM_PROVIDER, "model": LLM_MODEL}

    # 1. Download the image
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(image_url, timeout=15.0)
            response.raise_for_status()
            image_bytes = response.content
            image_mime_type = response.headers.get("content-type", "image/jpeg")
            image_base64 = base64.b64encode(image_bytes).decode('utf-8')
    except httpx.RequestError as e:
        log.error(f"Failed to download image from URL: {image_url}. Error: {e}")
        return None
    except Exception as e:
        log.error(f"Error processing image download: {e}", exc_info=True)
        return None

    # 2. Build Payload
    try:
        if LLM_PROVIDER == "google":
            headers = _build_google_headers(LLM_API_KEY)
            payload = _build_google_vision_payload(image_base64, image_mime_type)
        else: # Assumes openai or openrouter
            headers = _build_openai_headers(LLM_API_KEY)
            payload = _build_openai_vision_payload(image_base64, image_mime_type)
    except Exception as e:
        log.error(f"Failed to build Vision payload: {e}", exc_info=True)
        return None

    content_str = ""
    try:
        # 3. Call API
        async with httpx.AsyncClient() as client:
            response = await client.post(LLM_API_URL, headers=headers, json=payload, timeout=30.0)
            log_meta["status_code"] = response.status_code
            latency_ms = response.elapsed.total_seconds() * 1000
            log_meta["latency_ms"] = latency_ms

            if response.status_code != 200:
                log.error("Vision API error", extra={**log_meta, "response_text": response.text[:200]})
                telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "http_status"}))
                return None

            response_json = response.json()

            # 4. Extract Response
            if LLM_PROVIDER == "google":
                content_str = _extract_google_response(response_json)
            else:
                content_str = _extract_openai_response(response_json)

            m = re.search(r'\{.*\}', content_str, re.DOTALL)
            if not m:
                log.warning("No JSON block found in Vision response.", extra=log_meta)
                telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "no_json"}))
                return None
            content_str = m.group(0)

            parsed = json.loads(content_str)
            if isinstance(parsed, str) and parsed.strip().startswith('{'):
                parsed = json.loads(parsed)

            if isinstance(parsed, dict) and parsed.get("error"):
                reason = parsed.get("error")
                log.warning("Vision model reported error", extra={**log_meta, "reason": reason})
                telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "llm_reported", "detail": reason}))
                return None

            required = ["asset", "side", "entry", "stop_loss", "targets"]
            if not all(k in parsed for k in required):
                log.warning("Missing required keys in Vision output.", extra={**log_meta, "missing": [k for k in required if k not in parsed]})
                telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "missing_keys"}))
                return None

            # 5. ✅ CRITICAL: Re-use text normalization and validation
            # Note: We pass an empty string for `source_text` as we can't reliably
            # get global percentages from the image text itself via this prompt.
            parsed_targets_raw = parsed.get("targets")
            normalized_targets = normalize_targets(parsed_targets_raw, source_text="")
            parsed["targets"] = normalized_targets 

            entry_val = parse_decimal_token(str(parsed["entry"]))
            sl_val = parse_decimal_token(str(parsed["stop_loss"]))
            if entry_val is None or sl_val is None:
                log.warning(f"Entry/SL normalization failed.", extra=log_meta)
                telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "entry_sl_parse"}))
                return None
            parsed["entry"] = str(entry_val)
            parsed["stop_loss"] = str(sl_val)

            # 6. ✅ CRITICAL: Re-use financial consistency check
            if not _financial_consistency_check(parsed):
                log.warning("Financial consistency check failed for Vision parse.", extra=log_meta)
                telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "financial_check"}))
                return None

            parsed.setdefault("market", parsed.get("market", "Futures"))
            parsed.setdefault("order_type", parsed.get("order_type", "LIMIT"))
            parsed.setdefault("notes", parsed.get("notes", ""))

            telemetry_log.info(json.dumps({**log_meta, "success": True, "asset": parsed.get("asset"), "side": parsed.get("side"), "num_targets": len(parsed.get("targets", []))}))
            return parsed

    except httpx.RequestError as e:
        log.error(f"HTTP request failed: {e}", exc_info=True)
        telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "http_request_exception"}))
        return None
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        log.error(f"Failed parsing Vision response: {e}. Snippet: {content_str[:200]}", exc_info=True)
        telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "json_decode"}))
        return None
    except Exception as e:
        log.exception(f"Unexpected error in parse_with_vision: {e}")
        telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "unexpected"}))
        return None

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/services/image_parser.py ---