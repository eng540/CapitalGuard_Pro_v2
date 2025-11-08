# ai_service/services/llm_parser.py
"""
(v2.9.0 - Percentage Prompting Hotfix)
✅ HOTFIX: تم تحديث SYSTEM_PROMPT (v2.9) ليتضمن أمثلة صريحة
لكل من "(20% each)" و "كل هدف 25%" و "Close 30% each TP".
✅ REFACTORED: يعتمد الآن بشكل كامل على `parsing_utils.py` للتحليل.
"""

import os
import re
import json
import logging
from typing import Any, Dict, List, Optional
from decimal import Decimal, InvalidOperation 

import httpx

# --- ✅ استيراد مصدر الحقيقة الوحيد ---
from services.parsing_utils import (
    parse_decimal_token, 
    normalize_targets
)

log = logging.getLogger(__name__)
telemetry_log = logging.getLogger("ai_service.telemetry")

# Environment-driven LLM config
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "google").lower()
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_API_URL = os.getenv("LLM_API_URL")
LLM_MODEL = os.getenv("LLM_MODEL")

if not all([LLM_API_KEY, LLM_API_URL, LLM_MODEL]):
    log.warning("LLM environment variables incomplete. LLM parsing may be skipped.")

# --- ✅ (Point 6) UPDATED: موجه النظام الموحد (v2.9) ---
SYSTEM_PROMPT = os.getenv("LLM_SYSTEM_PROMPT") or """
You are an expert financial analyst. Your task is to extract structured data from a forwarded trade signal.
You must analyze the user's text and return ONLY a valid JSON object.

--- 
### CRITICAL VALIDATION RULES ###
1.  **Asset/Side/Entry/SL/Targets:** You *must* find all five fields. If any are missing, respond with `{"error": "Missing required fields."}`.
2.  **LONG Validation:** If "side" is "LONG", the "stop_loss" *must* be less than the "entry" price.
3.  **SHORT Validation:** If "side" is "SHORT", the "stop_loss" *must* be greater than the "entry" price.
4.  **Targets Validation:** All "targets" prices *must* be greater than "entry" for LONGs, and less than "entry" for SHORTs.
5.  **If any validation rule (2, 3, 4) fails, DO NOT return the data.** Instead, respond with `{"error": "Financial validation failed (e.g., SL vs Entry)."}`.
---

### EXTRACTION RULES ###
1.  Asset: Find the asset (e.g., "#ETH" -> "ETHUSDT", "#TURTLE" -> "TURTLEUSDT").
2.  Side: "LONG" or "SHORT".
3.  Entry: Find "Entry", "مناطق الدخول". If it's a range ("Entry 1", "Entry 2"), use *ONLY THE FIRST* price.
4.  Stop Loss: Find "SL", "ايقاف خسارة".
5.  Targets: Find "Targets", "TPs", "الاهداف". Extract *all* numbers that follow. Ignore ranges. Ignore emojis (✅).
6.  **Percentages (CRITICAL):**
    * If targets have individual percentages (e.g., "105k@10"), use them.
    * **If a *global* percentage is mentioned (e.g., "(20% each)", "(20% per target)", "Close 30% each TP", "كل هدف 25%"), you *MUST* apply that percentage to *ALL* targets in the list.**
    * If no percentages are found, default to 0.0 for all targets (the normalizer will handle the 100% rule).
7.  Notes: Add any extra text (like "Lev - 5x" or "لللحماية") to the "notes" field.
8.  Market/Order: Default to "Futures" and "LIMIT".
9.  **Arabic Logic:** "منطقة الدخول" = Entry. "وقف الخسارة" = Stop Loss.
10. **Inference Logic:** If "side" is missing, try to infer it (SL vs Entry).

--- 
### EXAMPLE 1 (Arabic - Global Percentage) ---
USER TEXT:
توصية عملة BNB
شراء من 940 إلى 960
وقف خسارة 910
الاهداف 1000 - 1100 - 1200 - 1400
كل هدف 25%

YOUR JSON RESPONSE:
{
  "asset": "BNBUSDT",
  "side": "LONG",
  "entry": "940",
  "stop_loss": "910",
  "targets": [
    {"price": "1000", "close_percent": 25.0},
    {"price": "1100", "close_percent": 25.0},
    {"price": "1200", "close_percent": 25.0},
    {"price": "1400", "close_percent": 25.0}
  ],
  "market": "Futures",
  "order_type": "LIMIT",
  "notes": null
}
--- 
### EXAMPLE 2 (English - Global Percentage) ---
USER TEXT:
#SOL LONG
Entry 172
Targets 185 - 200 - 220 - 250
SL 165
(20% per target)

YOUR JSON RESPONSE:
{
  "asset": "SOLUSDT",
  "side": "LONG",
  "entry": "172",
  "stop_loss": "165",
  "targets": [
    {"price": "185", "close_percent": 20.0},
    {"price": "200", "close_percent": 20.0},
    {"price": "220", "close_percent": 20.0},
    {"price": "250", "close_percent": 20.0}
  ],
  "market": "Futures",
  "order_type": "LIMIT",
  "notes": "(20% per target)"
}
---

The user's text will be provided next. Respond ONLY with the JSON object.
"""

# --- Financial consistency check ---
def _financial_consistency_check(data: Dict[str, Any]) -> bool:
    """
    Strict numeric checks (v4.1 - Refactored to use Decimal).
    """
    try:
        entry = Decimal(str(data["entry"]))
        sl = Decimal(str(data["stop_loss"]))
        side = str(data["side"]).strip().upper()
        targets_raw = data.get("targets", [])
        
        if not isinstance(targets_raw, list) or len(targets_raw) == 0:
            log.warning("Targets missing or empty in financial check.")
            return False

        prices: List[Decimal] = []
        for t in targets_raw:
            price_str = str(t["price"]) if isinstance(t, dict) else str(t)
            prices.append(Decimal(price_str))

        if entry <= 0 or sl <= 0:
            log.warning("Entry or SL non-positive.")
            return False

        if len(set(prices)) != len(prices):
            log.warning("Duplicate targets detected.")
            return False

        if len(prices) > 1:
            rng = abs(prices[-1] - prices[0])
            if rng < (entry * Decimal("0.002")):
                log.warning("Targets too close together.")
                return False

        if side == "LONG":
            if not (sl < entry):
                log.warning(f"LONG check failed: SL {sl} >= Entry {entry}")
                return False
            if any(p <= entry for p in prices):
                log.warning("At least one LONG target <= entry")
                return False
        elif side == "SHORT":
            if not (sl > entry):
                log.warning(f"SHORT check failed: SL {sl} <= Entry {entry}")
                return False
            if any(p >= entry for p in prices):
                log.warning("At least one SHORT target >= entry")
                return False
        else:
            log.warning(f"Invalid side value: {side}")
            return False

        return True
    except (InvalidOperation, TypeError, KeyError) as e:
        log.warning(f"Financial check exception: {e}. Data: {data}")
        return False

# --- LLM payload builders / extractors (remain the same) ---
def _build_google_headers(api_key: str) -> Dict[str, str]:
    return {"Content-Type": "application/json", "X-goog-api-key": api_key}
def _build_google_payload(text: str, system_prompt: str) -> Dict[str, Any]:
    return {"contents": [{"parts": [{"text": system_prompt}, {"text": "--- ACTUAL USER TEXT START ---"}, {"text": text}, {"text": "--- ACTUAL USER TEXT END ---"}]}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0.0}}
def _build_openai_headers(api_key: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
def _build_openai_payload(text: str, system_prompt: str) -> Dict[str, Any]:
    return {"model": LLM_MODEL, "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": text}], "response_format": {"type": "json_object"}}
def _extract_google_response(response_json: Dict[str, Any]) -> str:
    return response_json["candidates"][0]["content"]["parts"][0]["text"]
def _extract_openai_response(response_json: Dict[str, Any]) -> str:
    return response_json["choices"][0]["message"]["content"]

# --- Main parse function ---
async def parse_with_llm(text: str) -> Optional[Dict[str, Any]]:
    """
    Calls LLM provider, normalizes response and performs final validation.
    Returns normalized dict or None on failure.
    """
    if not all([LLM_API_KEY, LLM_API_URL, LLM_MODEL]):
        log.debug("LLM config incomplete; skipping LLM parse.")
        return None

    log_meta = {"event": "llm_parse", "provider": LLM_PROVIDER, "model": LLM_MODEL, "snippet": text[:120]}

    try:
        if LLM_PROVIDER == "google":
            headers = _build_google_headers(LLM_API_KEY)
            payload = _build_google_payload(text, SYSTEM_PROMPT)
        else:
            headers = _build_openai_headers(LLM_API_KEY)
            payload = _build_openai_payload(text, SYSTEM_PROMPT)
    except Exception as e:
        log.error(f"Failed build LLM payload: {e}", exc_info=True)
        return None

    content_str = ""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(LLM_API_URL, headers=headers, json=payload, timeout=20.0)
            log_meta["status_code"] = response.status_code
            latency_ms = response.elapsed.total_seconds() * 1000
            log_meta["latency_ms"] = latency_ms
            if latency_ms > 10000:
                log.warning("LLM slow response", extra=log_meta)

            if response.status_code != 200:
                log.error("LLM API error", extra={**log_meta, "response_text": response.text[:200]})
                telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "http_status"}))
                return None

            response_json = response.json()

            if LLM_PROVIDER == "google":
                content_str = _extract_google_response(response_json)
            else:
                content_str = _extract_openai_response(response_json)

            m = re.search(r'\{.*\}', content_str, re.DOTALL)
            if not m:
                log.warning("No JSON block found in LLM response.", extra=log_meta)
                telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "no_json"}))
                return None
            content_str = m.group(0)

            parsed = json.loads(content_str)
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

            # --- ✅ REFACTORED: Use unified normalizers ---
            # 1. Normalize targets using original text for context (e.g., "20% each")
            parsed_targets_raw = parsed.get("targets")
            normalized_targets = normalize_targets(parsed_targets_raw, source_text=text)
            parsed["targets"] = normalized_targets # (list of dicts with string prices)

            # 2. Normalize entry/sl (ensure they are valid Decimals, then convert to string)
            entry_val = parse_decimal_token(str(parsed["entry"]))
            sl_val = parse_decimal_token(str(parsed["stop_loss"]))
            if entry_val is None or sl_val is None:
                log.warning(f"Entry/SL normalization failed.", extra=log_meta)
                telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "entry_sl_parse"}))
                return None
            parsed["entry"] = str(entry_val)
            parsed["stop_loss"] = str(sl_val)
            # --- End Refactor ---

            # 3. Financial consistency check (uses the string-based dict)
            if not _financial_consistency_check(parsed):
                log.warning("Financial consistency check failed.", extra=log_meta)
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
        log.error(f"Failed parsing LLM response: {e}. Snippet: {content_str[:200]}", exc_info=True)
        telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "json_decode"}))
        return None
    except Exception as e:
        log.exception(f"Unexpected error in parse_with_llm: {e}")
        telemetry_log.info(json.dumps({**log_meta, "success": False, "error": "unexpected"}))
        return None