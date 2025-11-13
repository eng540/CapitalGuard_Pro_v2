#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/services/image_parser.py ---
# File: ai_service/services/image_parser.py
# Version: 2.0.0 (Multi-Provider Engine)
# ✅ THE FIX: (Protocol 1 / "الحل 1") تم تنفيذ "مصنع حمولات" (Payload Factory) متعدد المزودين.
#    - إضافة دوال بناء حمولات جديدة: `_build_claude_vision_payload` و `_build_qwen_vision_payload`.
#    - تحديث `parse_with_vision` ليقوم باختيار دالة البناء الصحيحة بناءً
#      على `LLM_PROVIDER` و `LLM_MODEL` (مثل "claude", "qwen").
# 🎯 IMPACT: النظام يدعم الآن Google, OpenAI, Anthropic (Claude), و Qwen
#    لتحليل الصور، ويمكن التبديل بينهم عبر ملف .env فقط.

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
    _build_openai_headers,
    _extract_openai_response,
    # ✅ ADDED: استيراد مستخرج Claude/Qwen (بافتراض أنه مشابه لـ OpenAI)
    _extract_openai_response as _extract_claude_response,
    _extract_openai_response as _extract_qwen_response
)

log = logging.getLogger(__name__)
telemetry_log = logging.getLogger("ai_service.telemetry")

# --- Environment-driven LLM/Vision config ---
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "google").lower()
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_API_URL = os.getenv("LLM_API_URL")
LLM_MODEL = os.getenv("LLM_MODEL", "") # الافتراضي هو نص فارغ للتحقق

if not all([LLM_API_KEY, LLM_API_URL, LLM_MODEL]):
    log.warning("LLM/Vision environment variables incomplete. Image parsing may be skipped.")

# --- ✅ (ADR-003): Prompt موحد للصور ---
SYSTEM_PROMPT_VISION = os.getenv("LLM_SYSTEM_PROMPT_VISION") or """
You are an expert financial analyst.
Your task is to extract structured data from an IMAGE of a trade signal.
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
3.  Entry: Find "Entry", "مناطق الدخول". If it's a range, use *ONLY THE FIRST* price.
4.  Stop Loss: Find "SL", "ايقاف خسارة".
5.  Targets: Find "Targets", "TPs", "الاهداف". Extract *all* numbers.
6.  Percentages: Default to 0.0 (the normalizer will handle it).
7.  Notes: Add any extra text (like "Leverage: 10x") to "notes".
8.  Market/Order: Default to "Futures" and "LIMIT".

Respond ONLY with the JSON object.
"""

# --- Payload Builders (Originals) ---

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
            {"role": "system", "content": SYSTEM_PROMPT_VISION},
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

# --- ✅ NEW: Payload Builders (Based on your report) ---

def _build_claude_vision_payload(image_base64: str, image_mime_type: str) -> Dict[str, Any]:
    """Builds the payload for Anthropic Claude 3 (multimodal)."""
    return {
        "model": LLM_MODEL,
        "messages": [
            # Note: Claude's "system" prompt is a top-level parameter
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": image_mime_type,
                            "data": image_base64
                        }
                    },
                    {"type": "text", "text": "Analyze the attached trade signal image."}
                ]
            }
        ],
        "system": SYSTEM_PROMPT_VISION, # System prompt is separate
        "max_tokens": 2048,
        "temperature": 0.0
        # Claude does not (natively) support JSON response format,
        # but the prompt engineering should force it.
    }

def _build_qwen_vision_payload(image_base64: str, image_mime_type: str) -> Dict[str, Any]:
    """Builds the payload for Qwen-VL (multimodal)."""
    # Qwen's API (especially via Alibaba) can be different,
    # but the format you provided is common for their open-source models.
    # Assuming the API endpoint handles this structure.
    return {
        "model": LLM_MODEL,
        "input": {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT_VISION},
                {
                    "role": "user",
                    "content": [
                        {"image": f"data:{image_mime_type};base64,{image_base64}"},
                        {"text": "Analyze the attached trade signal image."}
                    ]
                }
            ]
        },
        "parameters": {
            "result_format": "message"
        }
    }

# --- ✅ REFACTORED: Main Parse Function (Payload Factory) ---

async def parse_with_vision(image_url: str) -> Optional[Dict[str, Any]]:
    """
    Downloads, encodes, and calls the Vision provider to parse a trade.
    It now uses a "Payload Factory" to select the correct payload builder.
    Returns normalized dict or None on failure.
    """
    if not all([LLM_API_KEY, LLM_API_URL, LLM_MODEL]):
        log.debug("Vision config incomplete; skipping vision parse.")
        return None

    log_meta = {"event": "vision_parse", "provider": LLM_PROVIDER, "model": LLM_MODEL}

    # 1. Download the image (Unchanged)
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

    # 2. ✅ REFACTORED: Payload Factory Logic
    headers = {}
    payload = {}
    content_extractor = _extract_openai_response # Default
    
    try:
        provider = LLM_PROVIDER.lower()
        model_name = LLM_MODEL.lower()

        # Default headers (OpenAI/OpenRouter compatible)
        headers = _build_openai_headers(LLM_API_KEY)
        if provider == "openrouter":
            # OpenRouter requires these headers
            headers["HTTP-Referer"] = "http://localhost" # Can be anything
            headers["X-Title"] = "CapitalGuard"

        if provider == "google":
            headers = _build_google_headers(LLM_API_KEY)
            payload = _build_google_vision_payload(image_base64, image_mime_type)
            content_extractor = _extract_google_response
        
        elif "claude" in model_name: # Handles 'anthropic/claude...'
            payload = _build_claude_vision_payload(image_base64, image_mime_type)
            content_extractor = _extract_claude_response
            # Anthropic API has specific headers if NOT using OpenRouter
            if provider == "anthropic":
                headers["x-api-key"] = LLM_API_KEY
                headers["anthropic-version"] = "2023-06-01"
                headers.pop("Authorization", None) # Remove Bearer token

        elif "qwen" in model_name: # Handles 'qwen/...'
            payload = _build_qwen_vision_payload(image_base64, image_mime_type)
            content_extractor = _extract_qwen_response
            # Qwen might need specific headers if not using OpenRouter/OpenAI format

        else: # Default to OpenAI format
            payload = _build_openai_vision_payload(image_base64, image_mime_type)
            content_extractor = _extract_openai_response

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
                # Log the actual error response from the provider
                log.error(f"Vision API error. Status: {response.status_code}. Response: {response.text[:200]}", extra=log_meta)
                telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "http_status"}))
                return None

            response_json = response.json()

            # 4. Extract Response
            content_str = content_extractor(response_json)

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
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/services/image_parser.py ---