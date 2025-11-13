#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/services/llm_parser.py ---
# File: ai_service/services/llm_parser.py
# Version: 5.1.0 (v5.1 Engine Refactor)
# ✅ THE FIX: (Protocol 1) تم إصلاح التبعيات الدائرية (Circular Dependencies).
#    - 1. (DRY) تم حذف جميع الدوال المساعدة (مثل _build_google_headers, _extract_google_response).
#    - 2. (NEW) أصبح الآن يستدعي *فقط* الأدوات الموحدة من `parsing_utils`.
# 🎯 IMPACT: هذا الملف الآن "نظيف" (Clean) ومتوافق مع v5.1 Engine.

import os
import re
import json
import logging
from typing import Any, Dict, List, Optional
from decimal import Decimal

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
    # (الاستيرادات التي كانت مفقودة في utils)
    _extract_google_response,
    _extract_openai_response,
    _build_google_headers,
    _build_openai_headers
)

log = logging.getLogger(__name__)
telemetry_log = logging.getLogger("ai_service.telemetry")

# Environment-driven LLM config
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "google").lower()
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_API_URL = os.getenv("LLM_API_URL")
LLM_MODEL = os.getenv("LLM_MODEL", "")

if not all([LLM_API_KEY, LLM_API_URL, LLM_MODEL]):
    log.warning("LLM environment variables incomplete. LLM parsing may be skipped.")

# --- (v5.0) Prompt موحد للنصوص ---
SYSTEM_PROMPT_TEXT = os.getenv("LLM_SYSTEM_PROMPT_TEXT") or """
You are an expert financial analyst. Your task is to extract structured data from a forwarded trade signal (text).
Return ONLY a valid JSON object with fields: asset, side, entry, stop_loss, targets, notes (optional).

--- 
### CRITICAL VALIDATION RULES ###
1.  **Asset/Side/Entry/SL/Targets:** You *must* find all five fields. If any are missing, respond with `{"error": "Missing required fields."}`.
2.  **LONG Validation:** If "side" is "LONG", "stop_loss" *must* be less than "entry".
3.  **SHORT Validation:** If "side" is "SHORT", "stop_loss" *must* be greater than "entry".
4.  **If validation fails, DO NOT return the data.** Instead, respond with `{"error": "Financial validation failed (e.g., SL vs Entry)."}`.
---

### EXTRACTION RULES (FROM TEXT) ###
1.  Asset: Find the asset (e.g., "#ETH" -> "ETHUSDT", "#TURTLE" -> "TURTLEUSDT").
2.  Side: "LONG" or "SHORT".
3.  Entry: Find "Entry", "مناطق الدخول". If it's a range, use *ONLY THE FIRST* price.
4.  Stop Loss: Find "SL", "ايقاف خسارة".
5.  Targets: Find "Targets", "TPs", "الاهداف". Extract *all* numbers.
6.  **Percentages (CRITICAL):**
    * If a *global* percentage is mentioned (e.g., "(20% each)", "Close 30% each TP", "كل هدف 25%"), apply it to *ALL* targets.
    * If no percentages are found, default to 0.0 (normalizer will handle 100% rule).

Respond ONLY with the JSON object.
"""

# ------------------------
# Payload builders (provider-aware)
# ------------------------

def _build_google_text_payload(text: str) -> Dict[str, Any]:
    return {
        "contents": [
            {"parts": [
                {"text": SYSTEM_PROMPT_TEXT}, 
                {"text": "--- ACTUAL USER TEXT START ---"}, 
                {"text": text}, 
                {"text": "--- ACTUAL USER TEXT END ---"}
            ]}
        ],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.0}
    }

def _build_openai_text_payload(text: str) -> Dict[str, Any]:
    return {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_TEXT},
            {"role": "user", "content": text}
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 2048
    }

def _build_claude_text_payload(text: str) -> Dict[str, Any]:
    return {
        "model": LLM_MODEL,
        "system": SYSTEM_PROMPT_TEXT,
        "messages": [
            {"role": "user", "content": text}
        ],
        "max_tokens": 2048,
        "temperature": 0.0
    }

# ------------------------
# Main function: parse_with_llm (v5.0)
# ------------------------

async def parse_with_llm(text: str) -> Optional[Dict[str, Any]]:
    """
    Calls LLM provider using the v5.0 engine (retries, factory, safe extract).
    Returns normalized dict or None on failure.
    """
    if not all([LLM_API_KEY, LLM_API_URL, LLM_MODEL]):
        log.debug("LLM config incomplete; skipping LLM parse.")
        return None

    family = _model_family(LLM_MODEL)
    provider = (LLM_PROVIDER or "").lower()
    log_meta = {"event": "llm_parse", "provider": provider, "model": LLM_MODEL, "family": family, "snippet": text[:120]}
    
    # 1. Build Payload (Factory Logic)
    headers = {}
    payload = {}
    
    try:
        if provider == "google":
            headers = _headers_for_call("google_direct", LLM_API_KEY)
            payload = _build_google_text_payload(text)
        
        elif provider == "anthropic":
            headers = _headers_for_call("anthropic_direct", LLM_API_KEY)
            payload = _build_claude_text_payload(text)
        
        else: # Default to OpenAI format (covers "openai" and "openrouter")
            headers = _headers_for_call("openrouter_bearer" if provider == "openrouter" else "openai_direct", LLM_API_KEY)
            payload = _build_openai_text_payload(text)
            
    except Exception as e:
        log.error(f"Failed to build LLM text payload: {e}", exc_info=True)
        return None

    # 2. Call API (with Retries)
    # ✅ REFACTORED: Use unified retry mechanism
    success, resp_json, status, resp_text = await _post_with_retries(LLM_API_URL, headers, payload)
    log_meta["status_code"] = status
    
    if not success or not resp_json:
        log.error("LLM API call failed after retries.", extra=log_meta)
        telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "http_request_failed", "detail": resp_text[:200]}))
        return None

    # 3. Extract Response
    try:
        if family == "google":
            raw_text = _extract_google_response(resp_json)
        elif family == "anthropic":
            raw_text = _extract_claude_response(resp_json)
        elif family == "qwen":
            raw_text = _extract_qwen_response(resp_json)
        else: # Default to OpenAI
            raw_text = _extract_openai_response(resp_json)
    except Exception as e:
        log.exception(f"Extractor error: {e}")
        telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "extractor_exception"}))
        return None

    # 4. Safe JSON Extract
    # ✅ REFACTORED: Use unified safe extractor
    json_block = _safe_outer_json_extract(raw_text)
    if not json_block:
        log.warning(f"No JSON block found in LLM response. Snippet: {raw_text[:150]}", extra=log_meta)
        telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "no_json"}))
        return None

    # 5. Parse & Validate
    try:
        parsed = json.loads(json_block)
        if isinstance(parsed, str) and parsed.strip().startswith('{'):
            parsed = json.loads(parsed)

        if isinstance(parsed, dict) and parsed.get("error"):
            reason = parsed.get("error")
            log.warning("LLM-side reported error", extra={**log_meta, "reason": reason})
            telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "llm_reported", "detail": reason}))
            return None

        required = ["asset", "side", "entry", "stop_loss", "targets"]
        if not all(k in parsed for k in required):
            log.warning("Missing required keys in LLM output.", extra={**log_meta, "missing": [k for k in required if k not in parsed]})
            telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "missing_keys"}))
            return None

        # --- ✅ REFACTORED: Use unified normalizers (v5.0) ---
        # (Returns Decimals)
        parsed_targets_raw = parsed.get("targets")
        normalized_targets = normalize_targets(parsed_targets_raw, source_text=text)
        parsed["targets"] = normalized_targets 

        entry_val = parse_decimal_token(str(parsed["entry"]))
        sl_val = parse_decimal_token(str(parsed["stop_loss"]))
        
        if entry_val is None or sl_val is None:
            log.warning(f"Entry/SL normalization failed.", extra=log_meta)
            telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "entry_sl_parse"}))
            return None
        
        parsed["entry"] = entry_val
        parsed["stop_loss"] = sl_val
        # --- End Refactor ---

        # ✅ REFACTORED: Use unified financial check
        if not _financial_consistency_check(parsed):
            log.warning("Financial consistency check failed.", extra=log_meta)
            telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "financial_check"}))
            return None

        parsed.setdefault("market", parsed.get("market", "Futures"))
        parsed.setdefault("order_type", parsed.get("order_type", "LIMIT"))
        parsed.setdefault("notes", parsed.get("notes", ""))

        telemetry_log.info(json.dumps({**log_meta, "success": True, "asset": parsed.get("asset"), "side": parsed.get("side"), "num_targets": len(parsed.get("targets", []))}))
        
        # ✅ SUCCESS: Return dict with Decimals
        return parsed

    except (json.JSONDecodeError, ValueError, TypeError) as e:
        log.error(f"Failed parsing LLM response: {e}. Snippet: {json_block[:200]}", exc_info=True)
        telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "json_decode"}))
        return None
    except Exception as e:
        log.exception(f"Unexpected error in parse_with_llm: {e}")
        telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "unexpected"}))
        return None
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/services/llm_parser.py ---