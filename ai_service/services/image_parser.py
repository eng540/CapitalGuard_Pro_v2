#ai_service/services/image_parser.py

#Version: 3.0.0 (Multi-Provider Engine — Robust, Retries, Fixed Extractors)

""" Multi-provider Image Parser (v3.0.0)

Improvements in this release:

Correct and compatible payload builders for Google (Gemini 2.x), OpenAI (gpt-4o), Anthropic Claude (shape-compatible), and Qwen-VL styles.

Robust response extraction for multiple provider response shapes.

Exponential backoff + retry for transient HTTP errors (429/5xx).

Safer JSON extraction (find outermost JSON object using first '{' & last '}' to avoid accidental mid-text captures).

Size-check for images with graceful failure hint (avoid 413).

Clear telemetry of attempted providers and errors.

Automatic fallback: if provider=openrouter and model family=google and direct GOOGLE_API_KEY is present, then upon OpenRouter failure will attempt direct Google endpoint (transparent).

Defensive headers handling per-provider.

Re-uses parsing_utils normalization and llm_parser financial checks.


Environment variables used:

LLM_PROVIDER (openai | google | openrouter | anthropic)

LLM_API_KEY

LLM_API_URL

LLM_MODEL

GOOGLE_API_KEY (optional; used for direct Google fallback)

OPENAI_API_KEY (optional; used for direct OpenAI fallback)

IMAGE_PARSE_MAX_RETRIES (optional, default=3)

IMAGE_PARSE_BACKOFF_BASE (optional, default=1.0 seconds)


Return: normalized parsed dict on success, None on failure. """

import os
import re
import json
import logging
import base64
import asyncio
from typing import Any, Dict, Optional, Tuple, List
import httpx

# Reuse parsing utils and llm parsing helpers
from services.parsing_utils import parse_decimal_token, normalize_targets
from services.llm_parser import (
    _financial_consistency_check,
    _build_google_headers,
    _extract_google_response,
    _build_openai_headers,
    _extract_openai_response,
)

log = logging.getLogger(__name__)
telemetry_log = logging.getLogger("ai_service.telemetry")

# Environment-driven config
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "google").lower()
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_API_URL = os.getenv("LLM_API_URL")
LLM_MODEL = os.getenv("LLM_MODEL", "").strip().lower()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")  # optional direct Google key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # optional direct OpenAI key

# Retry/backoff config
try:
    IMAGE_PARSE_MAX_RETRIES = int(os.getenv("IMAGE_PARSE_MAX_RETRIES", "3"))
except Exception:
    IMAGE_PARSE_MAX_RETRIES = 3

try:
    IMAGE_PARSE_BACKOFF_BASE = float(os.getenv("IMAGE_PARSE_BACKOFF_BASE", "1.0"))
except Exception:
    IMAGE_PARSE_BACKOFF_BASE = 1.0

if not all([LLM_API_KEY, LLM_API_URL, LLM_MODEL]):
    log.warning("Vision env incomplete. Image parsing may be skipped or limited.")

# Vision system prompt (unchanged semantics)
SYSTEM_PROMPT_VISION = os.getenv("LLM_SYSTEM_PROMPT_VISION") or """ You are an expert financial analyst. Your task is to extract structured data from an IMAGE of a trade signal. The image may be a screenshot from another Telegram channel or trading platform. Analyze the text visible in the image and return ONLY a valid JSON object.


---

CRITICAL VALIDATION RULES

1. Asset/Side/Entry/SL/Targets: You must find all five fields. If any are missing, respond with {"error": "Missing required fields."}.


2. LONG Validation: If "side" is "LONG", "stop_loss" must be less than "entry".


3. SHORT Validation: If "side" is "SHORT", "stop_loss" must be greater than "entry".


4. Targets Validation: All "targets" prices must be greater than "entry" for LONGs, and less than "entry" for SHORTs.


5. If any validation rule (2, 3, 4) fails, DO NOT return the data. Instead, respond with {"error": "Financial validation failed (e.g., SL vs Entry)."}.




---

EXTRACTION RULES (FROM IMAGE)

1. Asset: Find the asset (e.g., "#ETH", "BTCUSDT").


2. Side: "LONG" or "SHORT", "Buy" or "Sell".


3. Entry: Find "Entry", "مناطق الدخول". If it's a range, use ONLY THE FIRST price.


4. Stop Loss: Find "SL", "ايقاف خسارة".


5. Targets: Find "Targets", "TPs", "الاهداف". Extract all numbers.


6. Percentages: Default to 0.0 (the normalizer will handle it).


7. Notes: Add any extra text (like "Leverage: 10x") to "notes".


8. Market/Order: Default to "Futures" and "LIMIT". Respond ONLY with the JSON object. """


# --------------------
# Payload builders (provider-specific)
# --------------------

def _build_google_vision_payload(image_base64: str, image_mime_type: str) -> Dict[str, Any]:
    return {
        "contents": [
            {
                "parts": [
                    {"text": SYSTEM_PROMPT_VISION},
                    {"inline_data": {"mime_type": image_mime_type, "data": image_base64}}
                ]
            }
        ],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.0}
    }


def _build_openai_vision_payload(image_base64: str, image_mime_type: str) -> Dict[str, Any]:
    return {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_VISION},
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_image",
                        "image_url": {"url": f"data:{image_mime_type};base64,{image_base64}"}
                    }
                ]
            }
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 2048
    }


def _build_claude_vision_payload(image_base64: str, image_mime_type: str) -> Dict[str, Any]:
    # Anthropic often uses a messages array; system as top-level optional.
    # Provide a safe structure that many Claude endpoints accept (may vary by gateway).
    return {
        "model": LLM_MODEL,
        "system": SYSTEM_PROMPT_VISION,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "data": image_base64, "mime_type": image_mime_type},
                    {"type": "text", "text": "Extract trade signal JSON from the attached image."}
                ]
            }
        ],
        "max_tokens": 2048,
        "temperature": 0.0
    }


def _build_qwen_vision_payload(image_base64: str, image_mime_type: str) -> Dict[str, Any]:
    # Qwen gateway variations exist; this structure covers commonly-used gateways.
    return {
        "model": LLM_MODEL,
        "input": [
            {"type": "image", "mime_type": image_mime_type, "data": image_base64},
            {"type": "text", "text": SYSTEM_PROMPT_VISION}
        ],
        "parameters": {"result_format": "json"}
    }


def _build_openrouter_openai_style_payload(image_base64: str, image_mime_type: str) -> Dict[str, Any]:
    # Useful when calling OpenRouter proxies which accept OpenAI-style payloads.
    return _build_openai_vision_payload(image_base64, image_mime_type)


# --------------------
# Extractors: robust handling of multiple response shapes
# --------------------

def _safe_outer_json_extract(s: str) -> Optional[str]:
    """ Extract outermost JSON object by finding first '{' and the matching last '}'.
    Safer than greedy regex; returns JSON substring or None. """
    if not s:
        return None
    first = s.find('{')
    last = s.rfind('}')
    if first == -1 or last == -1 or last <= first:
        return None
    return s[first:last+1]


def _extract_claude_response(response_json: Dict[str, Any]) -> str:
    # Try common Claude shapes, then fallback to stringified JSON
    try:
        if "completion" in response_json:
            # anthopic-like wrapper
            return response_json["completion"]
        if "choices" in response_json and isinstance(response_json["choices"], list):
            # some gateways use choices array
            return response_json["choices"][0].get("text", json.dumps(response_json))
        # fallback: stringify
        return json.dumps(response_json)
    except Exception:
        return json.dumps(response_json)


def _extract_qwen_response(response_json: Dict[str, Any]) -> str:
    try:
        # Common Qwen shapes: {"outputs":[{"content":"..."}]}
        if "outputs" in response_json and isinstance(response_json["outputs"], list):
            item = response_json["outputs"][0]
            if isinstance(item, dict):
                if "content" in item:
                    return item["content"]
                if "message" in item:
                    return item["message"]
        # Some gateways return 'result' or 'data'
        if "result" in response_json:
            return response_json["result"]
        return json.dumps(response_json)
    except Exception:
        return json.dumps(response_json)


# --------------------
# Helpers: model family detection and headers
# --------------------

def _model_family(model_name: str) -> str:
    mn = (model_name or "").lower()
    if not mn:
        return "unknown"
    if "gemini" in mn or mn.startswith("google/"):
        return "google"
    if mn.startswith("gpt-") or mn.startswith("openai/") or "gpt-4o" in mn:
        return "openai"
    if mn.startswith("anthropic") or "claude" in mn:
        return "anthropic"
    if mn.startswith("alibaba") or "qwen" in mn:
        return "qwen"
    return "other"


def _headers_for_call(call_style: str, api_key: str) -> Dict[str, str]:
    if call_style == "google_direct":
        return {"Content-Type": "application/json", "X-goog-api-key": api_key}
    if call_style == "openai_direct":
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if call_style == "openrouter_bearer":
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if call_style == "anthropic":
        return {"x-api-key": api_key, "Content-Type": "application/json"}
    return {"Content-Type": "application/json"}


# --------------------
# Core call with retries/backoff
# --------------------

async def _post_with_retries(url: str, headers: Dict[str, str], payload: Dict[str, Any], family: str) -> Tuple[bool, Optional[Dict[str, Any]], int, str]:
    """ Try POST with retries on 429/5xx with exponential backoff.
    Returns: (success, response_json or None, status_code, response_text_snippet) """
    attempt = 0
    last_text = ""
    while attempt <= IMAGE_PARSE_MAX_RETRIES:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, headers=headers, json=payload, timeout=30.0)
                status = resp.status_code
                text_snip = resp.text[:2000]
                last_text = text_snip
                if status == 200:
                    try:
                        return True, resp.json(), status, text_snip
                    except Exception:
                        return False, None, status, text_snip
                # transient
                if status in (429, 500, 502, 503, 504):
                    backoff = IMAGE_PARSE_BACKOFF_BASE * (2 ** attempt)
                    log.warning(f"Transient HTTP status {status}. Backing off {backoff}s. Attempt {attempt}/{IMAGE_PARSE_MAX_RETRIES}", extra={"status": status})
                    await asyncio.sleep(backoff)
                    attempt += 1
                    continue
                # other fatal status
                return False, None, status, text_snip
        except httpx.RequestError as e:
            # network-level error -> retry
            backoff = IMAGE_PARSE_BACKOFF_BASE * (2 ** attempt)
            log.warning(f"HTTP request error: {e}. Backoff {backoff}s. Attempt {attempt}/{IMAGE_PARSE_MAX_RETRIES}")
            await asyncio.sleep(backoff)
            attempt += 1
            last_text = str(e)
            continue
        except Exception as e:
            log.exception(f"Unexpected error during POST: {e}")
            return False, None, 0, str(e)
    return False, None, 0, last_text


# --------------------
# Main exposed function
# --------------------

async def parse_with_vision(image_url: str) -> Optional[Dict[str, Any]]:
    """ Downloads image, encodes it and calls the appropriate LLM Vision endpoint(s).
    Returns normalized dict or None on failure. """
    if not all([LLM_API_KEY, LLM_API_URL, LLM_MODEL]):
        log.debug("Vision config incomplete; skipping vision parse.")
        return None

    family = _model_family(LLM_MODEL)
    log_meta_base = {"event": "vision_parse", "provider": LLM_PROVIDER, "model": LLM_MODEL, "family": family, "image_url": image_url}
    attempted: List[Dict[str, Any]] = []
    final_errors: List[str] = []

    # 1) Download image
    try:
        async with httpx.AsyncClient() as client:
            get_resp = await client.get(image_url, timeout=20.0)
            get_resp.raise_for_status()
            image_bytes = get_resp.content
            mime = get_resp.headers.get("content-type", "image/jpeg") or "image/jpeg"
            # Quick size hint: warn if > 4.5MB (some providers have tight limits)
            size_bytes = len(image_bytes)
            if size_bytes > 4_500_000:
                log.warning("Image larger than 4.5MB. Consider resizing before sending to avoid 413 errors.", extra=log_meta_base)
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    except httpx.RequestError as e:
        log.error(f"Failed to download image: {e}", exc_info=True)
        telemetry_log.info(json.dumps({**log_meta_base, "success": False, "error": "download_failed"}))
        return None
    except Exception as e:
        log.exception(f"Error downloading/processing image: {e}")
        telemetry_log.info(json.dumps({**log_meta_base, "success": False, "error": "download_exception"}))
        return None

    # 2) Build list of candidate calls
    candidates: List[Tuple[str, Dict[str, str], Dict[str, Any], str]] = []
    try:
        prov = LLM_PROVIDER.lower()
        # If user explicitly selects google provider -> direct google call
        if prov == "google":
            headers = _headers_for_call("google_direct", LLM_API_KEY)
            payload = _build_google_vision_payload(image_b64, mime)
            candidates.append((LLM_API_URL, headers, payload, "google"))
        elif prov == "openai":
            key = OPENAI_API_KEY if OPENAI_API_KEY else LLM_API_KEY
            headers = _headers_for_call("openai_direct", key)
            payload = _build_openai_vision_payload(image_b64, mime)
            candidates.append((LLM_API_URL, headers, payload, "openai"))
        elif prov == "anthropic":
            # Assume direct Anthropic endpoint
            headers = _headers_for_call("anthropic", LLM_API_KEY)
            payload = _build_claude_vision_payload(image_b64, mime)
            candidates.append((LLM_API_URL, headers, payload, "anthropic"))
        elif prov == "openrouter":
            # Primary: try OpenAI-style payload via OpenRouter (works for many proxied families)
            headers_or = _headers_for_call("openrouter_bearer", LLM_API_KEY)
            or_payload = _build_openrouter_openai_style_payload(image_b64, mime)
            candidates.append((LLM_API_URL, headers_or, or_payload, "openai"))
            # If model family is google, add an OpenRouter attempt (may fail) but we'll fallback to direct Google if configured.
            if family == "google":
                candidates.append((LLM_API_URL, headers_or, or_payload, "openai"))  # explicit attempt
        else:
            # Unknown provider: attempt OpenAI-style via LLM_API_URL
            headers = _headers_for_call("openrouter_bearer", LLM_API_KEY)
            payload = _build_openrouter_openai_style_payload(image_b64, mime)
            candidates.append((LLM_API_URL, headers, payload, "openai"))
    except Exception as e:
        log.exception(f"Failed building candidate payloads: {e}")
        return None

    # 3) Iterate candidates, try direct fallback if needed (google)
    for api_url, headers, payload, call_family in candidates:
        log_meta = {**log_meta_base, "attempt_family": call_family, "api_url": api_url}
        telemetry_log.info(json.dumps({**log_meta, "attempt": "primary"}))
        success, resp_json, status, resp_text = await _post_with_retries(api_url, headers, payload, call_family)
        attempted.append({"api_url": api_url, "family": call_family, "status": status, "resp_snip": (resp_text or "")[:800]})
        if success and resp_json:
            # choose extractor based on family detected for this call
            try:
                if call_family == "google":
                    raw_text = _extract_google_response(resp_json)
                elif call_family == "openai":
                    raw_text = _extract_openai_response(resp_json)
                elif call_family == "anthropic":
                    raw_text = _extract_claude_response(resp_json) if "_extract_claude_response" in globals() else _extract_claude_response(resp_json)
                elif call_family == "qwen":
                    raw_text = _extract_qwen_response(resp_json)
                else:
                    raw_text = json.dumps(resp_json)
            except Exception as e:
                log.exception(f"Extractor failed: {e}")
                final_errors.append(f"extractor_exception:{e}")
                continue

            # robust JSON extraction
            json_block = _safe_outer_json_extract(raw_text)
            if not json_block:
                # last-resort: stringify and attempt to find JSON
                js = json.dumps(resp_json)
                json_block = _safe_outer_json_extract(js)
            if not json_block:
                final_errors.append(f"no_json_found_in_response_family_{call_family}_status_{status}")
                telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "no_json"}))
                continue

            # parse JSON
            try:
                parsed = json.loads(json_block)
                if isinstance(parsed, str) and parsed.strip().startswith('{'):
                    parsed = json.loads(parsed)
            except Exception as e:
                log.exception(f"JSON decode error after extraction: {e}")
                final_errors.append(f"json_decode_error:{e}")
                continue

            # check for LLM-level error field
            if isinstance(parsed, dict) and parsed.get("error"):
                reason = parsed.get("error")
                telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "llm_reported", "detail": reason}))
                final_errors.append(f"llm_reported:{reason}")
                continue

            # required fields
            required = ["asset", "side", "entry", "stop_loss", "targets"]
            if not all(k in parsed for k in required):
                telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "missing_keys", "missing": [k for k in required if k not in parsed]}))
                final_errors.append(f"missing_keys_in_{call_family}")
                continue

            # normalization and validation
            try:
                parsed_targets_raw = parsed.get("targets")
                normalized_targets = normalize_targets(parsed_targets_raw, source_text="")
                parsed["targets"] = normalized_targets
                entry_val = parse_decimal_token(str(parsed["entry"]))
                sl_val = parse_decimal_token(str(parsed["stop_loss"]))
                if entry_val is None or sl_val is None:
                    telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "entry_sl_parse"}))
                    final_errors.append("entry_sl_parse")
                    continue
                parsed["entry"] = str(entry_val)
                parsed["stop_loss"] = str(sl_val)
                if not _financial_consistency_check(parsed):
                    telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "financial_check"}))
                    final_errors.append("financial_check")
                    continue
                parsed.setdefault("market", parsed.get("market", "Futures"))
                parsed.setdefault("order_type", parsed.get("order_type", "LIMIT"))
                parsed.setdefault("notes", parsed.get("notes", ""))
                telemetry_log.info(json.dumps({**log_meta, "success": True, "asset": parsed.get("asset"), "side": parsed.get("side"), "num_targets": len(parsed.get("targets", []))}))
                return parsed
            except Exception as e:
                log.exception(f"Postprocess normalization/validation error: {e}")
                final_errors.append(f"postprocess_exception:{e}")
                continue
        else:
            # Primary call failed; if OpenRouter + google family and direct GOOGLE_API_KEY available -> try direct Google
            telemetry_log.info(json.dumps({**log_meta, "success": False, "status": status, "resp_snip": (resp_text or "")[:400]}))
            if LLM_PROVIDER == "openrouter" and family == "google" and GOOGLE_API_KEY:
                # attempt direct google
                google_url = os.getenv("GOOGLE_API_URL") or "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
                g_headers = _headers_for_call("google_direct", GOOGLE_API_KEY)
                g_payload = _build_google_vision_payload(image_b64, mime)
                telemetry_log.info(json.dumps({**log_meta, "attempt": "google_direct_fallback"}))
                success2, resp_json2, status2, resp_text2 = await _post_with_retries(google_url, g_headers, g_payload, "google")
                attempted.append({"api_url": google_url, "family": "google_direct", "status": status2, "resp_snip": (resp_text2 or "")[:800]})
                if success2 and resp_json2:
                    try:
                        raw_text2 = _extract_google_response(resp_json2)
                        json_block2 = _safe_outer_json_extract(raw_text2)
                        if json_block2:
                            parsed2 = json.loads(json_block2)
                            # normalization + validation same as above
                            parsed_targets_raw = parsed2.get("targets")
                            normalized_targets = normalize_targets(parsed_targets_raw, source_text="")
                            parsed2["targets"] = normalized_targets
                            entry_val = parse_decimal_token(str(parsed2["entry"]))
                            sl_val = parse_decimal_token(str(parsed2["stop_loss"]))
                            if entry_val is None or sl_val is None:
                                final_errors.append("google_direct:entry_sl_parse")
                            elif not _financial_consistency_check(parsed2):
                                final_errors.append("google_direct:financial_check")
                            else:
                                parsed2.setdefault("market", parsed2.get("market", "Futures"))
                                parsed2.setdefault("order_type", parsed2.get("order_type", "LIMIT"))
                                parsed2.setdefault("notes", parsed2.get("notes", ""))
                                telemetry_log.info(json.dumps({**log_meta, "success": True, "attempt": "google_direct_fallback", "asset": parsed2.get("asset"), "side": parsed2.get("side"), "num_targets": len(parsed2.get("targets", []))}))
                                return parsed2
                    except Exception as e:
                        log.exception(f"google_direct_fallback postprocess error: {e}")
                        final_errors.append(f"google_direct_postprocess:{e}")
                        continue

    # Nothing succeeded
    telemetry_log.info(json.dumps({**log_meta_base, "success": False, "attempted": attempted, "errors": final_errors}))
    log.warning(f"Vision parse failed for image {image_url}. Attempts: {attempted} Errors: {final_errors}")
    return None

# End of file