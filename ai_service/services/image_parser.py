#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/services/image_parser.py ---
# File: ai_service/services/image_parser.py
# Version: 5.1.0 (v5.1 Engine Refactor)
# ✅ THE FIX: (Protocol 1) تم إصلاح التبعيات الدائرية (Circular Dependencies).
#    - 1. (BLOCKER) تم حذف `from services.llm_parser import ...`.
#    - 2. (NEW) أصبح الآن يستدعي *فقط* الأدوات الموحدة من `parsing_utils`.
#    - 3. (MAINTAIN) الحفاظ على منطق `_smart_signal_selector` و `MimeType` من v5.0.
# 🎯 IMPACT: هذا الملف الآن "نظيف" (Clean) ويكسر التبعية الدائرية (Circular Dependency).

import os
import re
import json
import logging
import base64
import asyncio
from typing import Any, Dict, Optional, Tuple, List
import httpx

# --- ✅ استيراد مصدر الحقيقة الوحيد (v5.1) ---
from services.parsing_utils import (
    parse_decimal_token, 
    normalize_targets,
    _financial_consistency_check,
    _model_family,
    _headers_for_call,
    _post_with_retries,
    _safe_outer_json_extract,
    _extract_claude_response,
    _extract_qwen_response,
    _extract_google_response,
    _extract_openai_response,
    _build_google_headers,
    _build_openai_headers,
    _smart_signal_selector # (لإصلاح خطأ JSON Array)
)

log = logging.getLogger(__name__)
telemetry_log = logging.getLogger("ai_service.telemetry")

# Environment/config
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "google").lower()
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_API_URL = os.getenv("LLM_API_URL")
LLM_MODEL = os.getenv("LLM_MODEL", "").strip()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")       # optional direct fallback
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")       # optional direct fallback

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

# Prompt (v5.0)
SYSTEM_PROMPT_VISION = os.getenv("LLM_SYSTEM_PROMPT_VISION") or """ You are an expert financial analyst. Your task is to extract structured data from an IMAGE of a trade signal.
CRITICAL VALIDATION RULES:
1. Asset/Side/Entry/SL/Targets: You must find all five fields. If any are missing, respond with {"error": "Missing required fields."}.
2. LONG Validation: If "side" is "LONG", "stop_loss" must be less than "entry".
3. SHORT Validation: If "side" is "SHORT", "stop_loss" must be greater than "entry".
4. If validation fails, respond with {"error": "Financial validation failed (e.g., SL vs Entry)."}.

CRITICAL EXTRACTION RULES:
1. If the image contains multiple trade signals, extract ONLY THE FIRST COMPLETE signal that has all required fields (asset, side, entry, stop_loss, targets).
2. Prioritize signals with more targets.
3. Return ONLY ONE JSON object, never an array.

Respond ONLY with the JSON object. """


# ------------------------
# Payload builders (provider-aware)
# ------------------------

def _build_google_vision_payload(image_b64: str, mime: str) -> Dict[str, Any]:
    # ✅ (v5.0): Force a safe mime_type
    safe_mime = "image/jpeg" if mime not in ["image/jpeg", "image/png", "image/webp"] else mime
    return {
        "contents": [
            {"parts": [
                {"text": SYSTEM_PROMPT_VISION},
                {"inline_data": {"mime_type": safe_mime, "data": image_b64}}
            ]}
        ],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.0}
    }

def _build_openai_vision_payload(image_b64: str, mime: str) -> Dict[str, Any]:
    safe_mime = "image/jpeg" if mime not in ["image/jpeg", "image/png", "image/webp"] else mime
    return {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_VISION},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{safe_mime};base64,{image_b64}"}}
            ]}
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 2048
    }

def _build_claude_vision_payload(image_b64: str, mime: str) -> Dict[str, Any]:
    safe_mime = "image/jpeg" if mime not in ["image/jpeg", "image/png", "image/webp"] else mime
    return {
        "model": LLM_MODEL,
        "system": SYSTEM_PROMPT_VISION,
        "messages": [
            {"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": safe_mime,
                    "data": image_b64,
                }},
                {"type": "text", "text": "Extract trade signal JSON from the attached image."}
            ]}
        ],
        "max_tokens": 2048,
        "temperature": 0.0
    }

def _build_openrouter_openai_style_payload(image_b64: str, mime: str) -> Dict[str, Any]:
    return _build_openai_vision_payload(image_b64, mime)

# ------------------------
# Main function: parse_with_vision
# ------------------------

async def parse_with_vision(image_url: str) -> Optional[Dict[str, Any]]:
    """ Downloads image, encodes, and calls provider endpoint(s). """
    if not all([LLM_API_KEY, LLM_API_URL, LLM_MODEL]):
        log.debug("Vision configuration incomplete; skipping vision parse.")
        return None

    family = _model_family(LLM_MODEL)
    provider = (LLM_PROVIDER or "").lower()
    log_meta_base = {"event": "vision_parse", "provider": provider, "model": LLM_MODEL, "family": family, "image_url": image_url}
    attempted: List[Dict[str, Any]] = []
    final_errors: List[str] = []

    # 1) Download image
    try:
        async with httpx.AsyncClient() as client:
            get_resp = await client.get(image_url, timeout=20.0)
            get_resp.raise_for_status()
            image_bytes = get_resp.content
            
            # ✅ THE FIX (v5.0): Force a safe mime_type
            _original_mime = get_resp.headers.get("content-type", "image/jpeg") or "image/jpeg"
            mime = "image/jpeg" # Use a safe default
            if _original_mime in ["image/png", "image/webp"]:
                mime = _original_mime
            elif _original_mime != "image/jpeg":
                log.info(f"Detected image format: {mime} (was: {_original_mime})")

            size_bytes = len(image_bytes)
            if size_bytes > 4_500_000:
                log.warning("Image larger than 4.5MB; consider resizing to avoid provider limits.", extra=log_meta_base)
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    except httpx.RequestError as e:
        log.error(f"Failed to download image: {e}", exc_info=True)
        telemetry_log.info(json.dumps({**log_meta_base, "success": False, "error": "download_failed"}))
        return None
    except Exception as e:
        log.exception(f"Image download/processing error: {e}")
        telemetry_log.info(json.dumps({**log_meta_base, "success": False, "error": "download_exception"}))
        return None

    # 2) Build candidate calls
    candidates: List[Tuple[str, Dict[str, str], Dict[str, Any], str]] = []
    try:
        prov = provider
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
            headers = _headers_for_call("anthropic_direct", LLM_API_KEY)
            payload = _build_claude_vision_payload(image_b64, mime)
            candidates.append((LLM_API_URL, headers, payload, "anthropic"))
            
        elif prov == "openrouter":
            headers_or = _headers_for_call("openrouter_bearer", LLM_API_KEY)
            payload_or = _build_openrouter_openai_style_payload(image_b64, mime)
            candidates.append((LLM_API_URL, headers_or, payload_or, family if family != "unknown" else "openai"))
        
        else:
            headers = _headers_for_call("openrouter_bearer", LLM_API_KEY)
            payload = _build_openrouter_openai_style_payload(image_b64, mime)
            candidates.append((LLM_API_URL, headers, payload, "openai"))
            
    except Exception as e:
        log.exception(f"Failed to build candidate payloads: {e}")
        return None

    # 3) Iterate candidates and apply fallback logic
    for api_url, headers, payload, call_family in candidates:
        meta = {**log_meta_base, "api_url": api_url, "attempt_family": call_family}
        telemetry_log.info(json.dumps({**meta, "attempt": "primary"}))
        
        success, resp_json, status, resp_text = await _post_with_retries(api_url, headers, payload)
        attempted.append({"api_url": api_url, "family": call_family, "status": status, "resp_snip": (resp_text or "")[:800]})
        
        if success and resp_json:
            try:
                if call_family == "google":
                    raw_text = _extract_google_response(resp_json)
                elif call_family == "anthropic":
                    raw_text = _extract_claude_response(resp_json)
                elif call_family == "qwen":
                    raw_text = _extract_qwen_response(resp_json)
                else: # Default to OpenAI
                    raw_text = _extract_openai_response(resp_json)
            except Exception as e:
                log.exception(f"Extractor error: {e}")
                final_errors.append(f"extractor_exception:{e}")
                continue

            json_block = _safe_outer_json_extract(raw_text)
            if not json_block:
                final_errors.append(f"no_json_family_{call_family}_status_{status}")
                telemetry_log.info(json.dumps({**meta, "success": False, "error": "no_json", "snippet": raw_text[:200]}))
                continue

            try:
                parsed_json = json.loads(json_block)
                if isinstance(parsed_json, str) and parsed_json.strip().startswith(('{', '[')):
                    parsed_json = json.loads(parsed_json)
            except Exception as e:
                log.error(f"JSON decode error after extraction: {e}. Snippet: {json_block[:200]}", exc_info=True)
                final_errors.append(f"json_decode:{e}")
                continue
            
            # ✅ THE FIX (v5.0): Handle JSON Array response
            parsed_object = None
            if isinstance(parsed_json, list):
                log.info(f"Received array of {len(parsed_json)} signals. Selecting best candidate.")
                selected_signal = _smart_signal_selector(parsed_json)
                if selected_signal:
                    parsed_object = selected_signal
                    log.info(f"Selected signal: {parsed_object.get('asset')}")
                else:
                    log.warning("JSON array received, but no valid/complete signals found.")
                    final_errors.append("no_valid_signal_in_array")
                    telemetry_log.info(json.dumps({**meta, "success": False, "error": "no_valid_signal_in_array"}))
                    continue # Try next candidate
            elif isinstance(parsed_json, dict):
                parsed_object = parsed_json
            else:
                log.warning(f"Extracted JSON is not a list or dict. Type: {type(parsed_json)}")
                final_errors.append("invalid_json_type")
                continue
            # --- End v5.0 Fix ---

            if parsed_object.get("error"):
                final_errors.append(f"llm_reported:{parsed_object.get('error')}")
                telemetry_log.info(json.dumps({**meta, "success": False, "error": "llm_reported", "detail": parsed_object.get('error')}))
                continue

            required = ["asset", "side", "entry", "stop_loss", "targets"]
            if not all(k in parsed_object for k in required):
                final_errors.append(f"missing_keys_family_{call_family}")
                telemetry_log.info(json.dumps({**meta, "success": False, "error": "missing_keys", "missing": [k for k in required if k not in parsed_object]}))
                continue

            try:
                parsed_targets_raw = parsed_object.get("targets")
                parsed_object["targets"] = normalize_targets(parsed_targets_raw, source_text="")
                entry_val = parse_decimal_token(str(parsed_object["entry"]))
                sl_val = parse_decimal_token(str(parsed_object["stop_loss"]))
                if entry_val is None or sl_val is None:
                    final_errors.append("entry_sl_parse_error")
                    telemetry_log.info(json.dumps({**meta, "success": False, "error": "entry_sl_parse"}))
                    continue
                parsed_object["entry"] = entry_val # Return Decimal
                parsed_object["stop_loss"] = sl_val # Return Decimal
                
                if not _financial_consistency_check(parsed_object):
                    final_errors.append("financial_consistency_failed")
                    telemetry_log.info(json.dumps({**meta, "success": False, "error": "financial_check"}))
                    continue

                parsed_object.setdefault("market", parsed_object.get("market", "Futures"))
                parsed_object.setdefault("order_type", parsed_object.get("order_type", "LIMIT"))
                parsed_object.setdefault("notes", parsed_object.get("notes", ""))
                
                telemetry_log.info(json.dumps({**meta, "success": True, "asset": parsed_object.get("asset"), "side": parsed_object.get("side"), "num_targets": len(parsed_object.get("targets", []))}))
                return parsed_object  # ✅ SUCCESS (Returns dict with Decimals)

            except Exception as e:
                log.exception(f"Postprocess error: {e}")
                final_errors.append(f"postprocess_exception:{e}")
                continue
        
        else: # Primary call failed
            telemetry_log.info(json.dumps({**meta, "success": False, "status": status, "resp_snip": (resp_text or "")[:400]}))

            # Fallback Logic
            if provider == "openrouter" and GOOGLE_API_KEY:
                google_url = os.getenv("GOOGLE_API_URL_FALLBACK", "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent")
                g_headers = _headers_for_call("google_direct", GOOGLE_API_KEY)
                g_payload = _build_google_vision_payload(image_b64, mime)
                
                log_meta_fallback = {**log_meta_base, "attempt": "google_direct_fallback", "api_url": google_url}
                telemetry_log.info(json.dumps(log_meta_fallback))
                
                success2, resp_json2, status2, resp_text2 = await _post_with_retries(google_url, g_headers, g_payload)
                attempted.append({"api_url": google_url, "family": "google_direct", "status": status2, "resp_snip": (resp_text2 or "")[:800]})
                
                if success2 and resp_json2:
                    try:
                        raw2 = _extract_google_response(resp_json2)
                        jb2 = _safe_outer_json_extract(raw2)
                        if jb2:
                            parsed2_json = json.loads(jb2)
                            
                            parsed2 = None
                            if isinstance(parsed2_json, list):
                                parsed2 = _smart_signal_selector(parsed2_json)
                            elif isinstance(parsed2_json, dict):
                                parsed2 = parsed2_json
                            
                            if not parsed2:
                                final_errors.append("google_direct:no_valid_signal")
                                continue

                            if not all(k in parsed2 for k in required): continue
                            parsed2["targets"] = normalize_targets(parsed2.get("targets"), source_text="")
                            entry_v = parse_decimal_token(str(parsed2["entry"]))
                            sl_v = parse_decimal_token(str(parsed2["stop_loss"]))
                            if entry_v is None or sl_v is None: continue
                            parsed2["entry"] = entry_v # Return Decimal
                            parsed2["stop_loss"] = sl_v # Return Decimal
                            if not _financial_consistency_check(parsed2): continue
                            
                            parsed2.setdefault("market", parsed2.get("market", "Futures"))
                            parsed2.setdefault("order_type", parsed2.get("order_type", "LIMIT"))
                            parsed2.setdefault("notes", parsed2.get("notes", ""))
                            
                            telemetry_log.info(json.dumps({**log_meta_fallback, "success": True, "asset": parsed2.get("asset")}))
                            return parsed2 # ✅ SUCCESS (on fallback)
                            
                    except Exception as e:
                        log.exception(f"google_direct_fallback postprocess error: {e}")
                        final_errors.append(f"google_direct_postprocess:{e}")
                        continue

    # nothing succeeded
    telemetry_log.info(json.dumps({**log_meta_base, "success": False, "attempted": attempted, "errors": final_errors}))
    log.warning(f"Vision parse failed for image {image_url}. Attempts: {len(attempted)} Errors: {final_errors}")
    return None
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/services/image_parser.py ---