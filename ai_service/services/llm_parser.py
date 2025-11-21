# File: ai_service/services/llm_parser.py
# Version: 5.2.0 (Comprehensive Data Quality Fix)
# ✅ THE FIX: إضافة التحقق الشامل من جودة البيانات وإصلاح JSON

import os
import re
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from decimal import Decimal

import httpx

# ✅ استيراد الأدوات المحسنة
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
    DataQualityMonitor  # ✅ NEW
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

# --- IMPROVED SYSTEM PROMPT ---
SYSTEM_PROMPT_TEXT = os.getenv("LLM_SYSTEM_PROMPT_TEXT") or """
You are an expert financial analyst. Your task is to extract structured data from a forwarded trade signal (text).

🚨 **CRITICAL REQUIREMENTS - READ CAREFULLY:** 🚨

You MUST return a COMPLETE JSON object with ALL these fields filled:
{
  "asset": "SYMBOLUSDT",    # REQUIRED: Convert #ETH → ETHUSDT, #BTC → BTCUSDT
  "side": "LONG|SHORT",     # REQUIRED: ONLY "LONG" or "SHORT" 
  "entry": 12345.67,        # REQUIRED: Number > 0
  "stop_loss": 12000.50,    # REQUIRED: Number > 0  
  "targets": [              # REQUIRED: Array of targets
    {"price": 13000.0, "close_percent": 25.0},
    {"price": 13500.0, "close_percent": 25.0},
    {"price": 14000.0, "close_percent": 50.0}
  ]
}

🔒 **VALIDATION RULES (NON-NEGOTIABLE):**
1. ALL 5 fields above MUST be present and non-null
2. If ANY field is missing, return: {"error": "Missing required field: X"}
3. LONG: stop_loss MUST be < entry
4. SHORT: stop_loss MUST be > entry  
5. ALL numbers MUST be positive
6. targets MUST have at least one entry

❌ FAILURE TO FOLLOW THESE RULES WILL RESULT IN AUTOMATIC REJECTION.

Respond ONLY with the valid JSON object. No other text.
"""

# ------------------------
# Payload builders
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
# Enhanced Data Processor
# ------------------------

class EnhancedDataProcessor:
    """✅ NEW: Comprehensive data processing and validation"""
    
    @staticmethod
    def enforce_data_types(data: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        """
        Enforces correct data types and returns (enforced_data, error_message)
        """
        try:
            enforced = data.copy()
            
            # Asset validation
            asset = str(data.get("asset", "")).strip().upper()
            if not asset:
                return {}, "Asset is empty"
            if not re.match(r'^[A-Z0-9]{2,20}$', asset):
                # Try to append USDT if short symbol
                if len(asset) <= 6 and not asset.endswith(('USDT', 'USD', 'BUSD')):
                    asset = f"{asset}USDT"
            enforced["asset"] = asset
            
            # Side validation
            side = str(data.get("side", "")).upper().strip()
            if side not in ["LONG", "SHORT"]:
                return {}, f"Invalid side: {side}. Must be LONG or SHORT"
            enforced["side"] = side
            
            # Entry validation
            entry = parse_decimal_token(str(data.get("entry", "")))
            if entry is None or entry <= 0:
                return {}, f"Invalid entry: {data.get('entry')}"
            enforced["entry"] = entry
            
            # Stop loss validation
            sl = parse_decimal_token(str(data.get("stop_loss", "")))
            if sl is None or sl <= 0:
                return {}, f"Invalid stop_loss: {data.get('stop_loss')}"
            enforced["stop_loss"] = sl
            
            # Targets validation and normalization
            targets_raw = data.get("targets", [])
            if not isinstance(targets_raw, list) or len(targets_raw) == 0:
                return {}, "Targets must be a non-empty list"
                
            normalized_targets = normalize_targets(targets_raw, source_text="")
            if not normalized_targets:
                return {}, "No valid targets found after normalization"
            enforced["targets"] = normalized_targets
            
            return enforced, ""
            
        except Exception as e:
            return {}, f"Data type enforcement failed: {str(e)}"

# ------------------------
# Main function: parse_with_llm (ENHANCED)
# ------------------------

async def parse_with_llm(text: str) -> Optional[Dict[str, Any]]:
    """
    Enhanced LLM parser with comprehensive data validation
    """
    if not all([LLM_API_KEY, LLM_API_URL, LLM_MODEL]):
        log.debug("LLM config incomplete; skipping LLM parse.")
        return None

    family = _model_family(LLM_MODEL)
    provider = (LLM_PROVIDER or "").lower()
    log_meta = {"event": "llm_parse", "provider": provider, "model": LLM_MODEL, "family": family, "snippet": text[:120]}
    
    # 1. Build Payload
    headers = {}
    payload = {}
    
    try:
        if provider == "google":
            headers = _headers_for_call("google_direct", LLM_API_KEY)
            payload = _build_google_text_payload(text)
        elif provider == "anthropic":
            headers = _headers_for_call("anthropic_direct", LLM_API_KEY)
            payload = _build_claude_text_payload(text)
        else:
            headers = _headers_for_call("openrouter_bearer" if provider == "openrouter" else "openai_direct", LLM_API_KEY)
            payload = _build_openai_text_payload(text)
    except Exception as e:
        log.error(f"Failed to build LLM text payload: {e}", exc_info=True)
        return None

    # 2. Call API
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
        else:
            raw_text = _extract_openai_response(resp_json)
    except Exception as e:
        log.exception(f"Extractor error: {e}")
        telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "extractor_exception"}))
        return None

    # 4. Safe JSON Extract with Enhanced Repair
    json_block = _safe_outer_json_extract(raw_text)
    if not json_block:
        log.warning(f"No JSON block found in LLM response. Snippet: {raw_text[:150]}", extra=log_meta)
        telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "no_json"}))
        return None

    # 5. Parse & Validate with Comprehensive Checks
    try:
        # ✅ Enhanced JSON parsing with multiple repair attempts
        parsed = None
        repair_attempts = [
            json_block,  # Original
            json_block.strip(),  # Stripped
            json_block + '}' if not json_block.endswith('}') else json_block,  # Add missing brace
        ]
        
        for attempt, json_attempt in enumerate(repair_attempts):
            try:
                parsed = json.loads(json_attempt)
                if attempt > 0:
                    log.info(f"✅ JSON repair successful on attempt {attempt + 1}")
                break
            except json.JSONDecodeError as e:
                if attempt == len(repair_attempts) - 1:
                    log.error(f"❌ All JSON repair attempts failed: {e}")
                    telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "json_decode_after_repair"}))
                    return None
                continue

        # Handle string-wrapped JSON
        if isinstance(parsed, str) and parsed.strip().startswith('{'):
            try:
                parsed = json.loads(parsed)
            except json.JSONDecodeError:
                log.error("Nested JSON string also failed to parse")
                return None

        # Check for LLM-reported errors
        if isinstance(parsed, dict) and parsed.get("error"):
            reason = parsed.get("error")
            log.warning("LLM-side reported error", extra={**log_meta, "reason": reason})
            telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "llm_reported", "detail": reason}))
            return None

        # ✅ STEP 1: Data Quality Validation
        is_valid, validation_reason = DataQualityMonitor.validate_llm_output(parsed)
        if not is_valid:
            log.warning(f"Data quality validation failed: {validation_reason}")
            telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "data_quality", "detail": validation_reason}))
            return None

        # ✅ STEP 2: Data Type Enforcement
        processor = EnhancedDataProcessor()
        enforced_data, enforcement_error = processor.enforce_data_types(parsed)
        if enforcement_error:
            log.warning(f"Data type enforcement failed: {enforcement_error}")
            telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "data_enforcement", "detail": enforcement_error}))
            return None

        # ✅ STEP 3: Financial Consistency Check
        if not _financial_consistency_check(enforced_data):
            log.warning("Financial consistency check failed.", extra=log_meta)
            telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "financial_check"}))
            return None

        # ✅ STEP 4: Final Data Preparation
        enforced_data.setdefault("market", enforced_data.get("market", "Futures"))
        enforced_data.setdefault("order_type", enforced_data.get("order_type", "LIMIT"))
        enforced_data.setdefault("notes", enforced_data.get("notes", ""))

        # ✅ SUCCESS
        telemetry_log.info(json.dumps({
            **log_meta, 
            "success": True, 
            "asset": enforced_data.get("asset"), 
            "side": enforced_data.get("side"), 
            "num_targets": len(enforced_data.get("targets", [])),
            "entry": str(enforced_data.get("entry")),
            "stop_loss": str(enforced_data.get("stop_loss"))
        }))
        
        log.info(f"✅ LLM parse successful: {enforced_data.get('asset')} {enforced_data.get('side')} "
                f"Entry:{enforced_data.get('entry')} SL:{enforced_data.get('stop_loss')} "
                f"Targets:{len(enforced_data.get('targets', []))}")
        
        return enforced_data

    except (json.JSONDecodeError, ValueError, TypeError) as e:
        log.error(f"Failed parsing LLM response: {e}. Snippet: {json_block[:200]}", exc_info=True)
        telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "json_decode"}))
        return None
    except Exception as e:
        log.exception(f"Unexpected error in parse_with_llm: {e}")
        telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "unexpected"}))
        return None