# ai_service/services/llm_parser.py
"""
خدمة الاتصال بـ LLM (v2.5.0 - Smart Financial Parser).
✅ (Point 1) يضيف التحقق المالي الرقمي (`_financial_consistency_check`).
✅ (Point 2 & 5) يضيف تصنيف الأخطاء والتسجيل المنظم (Structured Logging).
✅ (Point 3) يضيف موجه (Prompt) v2.5 مع قواعد استدلال عربية محسنة.
✅ (Point 4) يضيف مرشح Non-JSON (`re.search`) لزيادة الموثوقية.
"""

import os
import httpx
import logging
import json
import re # ✅ (Point 4)
from typing import Dict, Any, Optional

log = logging.getLogger(__name__)

# --- قراءة جميع متغيرات البيئة ---
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "google").lower()
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_API_URL = os.getenv("LLM_API_URL")
LLM_MODEL = os.getenv("LLM_MODEL") 

if not LLM_API_KEY or not LLM_API_URL or not LLM_MODEL:
    log.warning("LLM environment variables (KEY, URL, MODEL) are not fully set. LLM parser will be disabled.")

# --- ✅ (Point 3) UPDATED: موجه النظام الموحد (v2.5) ---
SYSTEM_PROMPT = """
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
5.  Targets: Find "Targets", "TPs", "الاهداف". Extract *all* numbers that follow. Ignore ranges. Ignore emojis (✅). If no targets are found, validation rule #1 fails.
6.  Percentages: Extract "close_percent" if available (e.g., "@50%" or "(25% each)"). Default to 0.0.
7.  Notes: Add any extra text (like "Lev - 5x" or "لللحماية") to the "notes" field.
8.  Market/Order: Default to "Futures" and "LIMIT".
9.  **Arabic Logic:** "منطقة الدخول" or "دخول" = Entry. "وقف الخسارة" or "ايقاف خسارة" = Stop Loss.
10. **Inference Logic:** If "side" is missing, try to infer it. If "ايقاف خسارة" (SL) is *below* "دخول" (Entry), it's a "LONG" trade. If SL is *above* Entry, it's a "SHORT" trade.

--- 
(Examples from v2.4 are still valid and included here)
...
---

The user's text will be provided next. Respond ONLY with the JSON object.
"""

# --- ✅ (Point 1) Hard-coded Financial Validation ---
def _financial_consistency_check(data: Dict[str, Any]) -> bool:
    """
    يقوم بإجراء تحقق رقمي صارم بعد استلام الـ JSON.
    هذا هو "جدار الحماية" الخاص بنا ضد هلوسات LLM.
    """
    try:
        entry = float(data["entry"])
        sl = float(data["stop_loss"])
        side = data["side"].upper()
        # تأكد من أن الأهداف موجودة وأنها قائمة
        targets_raw = data.get("targets")
        if not targets_raw or not isinstance(targets_raw, list):
             log.warning("Post-validation failed: Targets are missing or not a list.")
             return False
             
        targets = [float(t["price"]) for t in targets_raw]
        if not targets:
             log.warning("Post-validation failed: Targets list is empty.")
             return False # يجب أن تحتوي على هدف واحد على الأقل

        if side == "LONG":
            if sl >= entry:
                log.warning(f"Post-validation failed (LONG): SL {sl} >= Entry {entry}")
                return False
            if any(t <= entry for t in targets):
                log.warning(f"Post-validation failed (LONG): A target is <= Entry {entry}")
                return False
        elif side == "SHORT":
            if sl <= entry:
                log.warning(f"Post-validation failed (SHORT): SL {sl} <= Entry {entry}")
                return False
            if any(t >= entry for t in targets):
                log.warning(f"Post-validation failed (SHORT): A target is >= Entry {entry}")
                return False
        else:
            log.warning(f"Post-validation failed: Invalid side '{side}'")
            return False
        
        # إذا نجحت جميع الفحوصات
        return True
    except (ValueError, TypeError, KeyError) as e:
        log.warning(f"Post-validation failed due to data error: {e}. Data: {data}")
        return False


# --- المنطق الخاص بـ Google ---
def _build_google_headers(api_key: str) -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-goog-api-key": api_key
    }

def _build_google_payload(text: str) -> Dict[str, Any]:
    return {
        "contents": [
            {
                "parts": [
                    {"text": SYSTEM_PROMPT},
                    {"text": "--- ACTUAL USER TEXT START ---"},
                    {"text": text},
                    {"text": "--- ACTUAL USER TEXT END ---"}
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.0 
        }
    }

def _extract_google_response(response_json: Dict[str, Any]) -> str:
    try:
        return response_json["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        log.error(f"Failed to extract text from Google response structure: {e}. Response: {response_json}")
        raise ValueError("Invalid response structure from Google API.") from e


# --- المنطق الخاص بـ OpenAI / OpenRouter ---
def _build_openai_headers(api_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

def _build_openai_payload(text: str) -> Dict[str, Any]:
    return {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text}
        ],
        "response_format": {"type": "json_object"}
    }

def _extract_openai_response(response_json: Dict[str, Any]) -> str:
    try:
        return response_json["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        log.error(f"Failed to extract text from OpenAI response structure: {e}. Response: {response_json}")
        raise ValueError("Invalid response structure from OpenAI API.") from e


# --- الدالة الرئيسية (الموزع) ---

async def parse_with_llm(text: str) -> Optional[Dict[str, Any]]:
    """
    يستدعي واجهة برمجة تطبيقات LLM المناسبة بناءً على LLM_PROVIDER.
    """
    if not all([LLM_API_KEY, LLM_API_URL, LLM_MODEL]):
        log.debug("LLM parsing skipped: Environment variables incomplete.")
        return None

    headers: Dict[str, str]
    payload: Dict[str, Any]
    log_meta = {"event": "LLM_REQUEST", "provider": LLM_PROVIDER, "model": LLM_MODEL}

    try:
        if LLM_PROVIDER == "google":
            headers = _build_google_headers(LLM_API_KEY)
            payload = _build_google_payload(text)
        elif LLM_PROVIDER in ("openai", "openrouter"):
            headers = _build_openai_headers(LLM_API_KEY)
            payload = _build_openai_payload(text)
        else:
            log.error(f"Unsupported LLM_PROVIDER: {LLM_PROVIDER}.")
            return None
    except Exception as e:
        log.error(f"Failed to build LLM payload: {e}", exc_info=True)
        return None

    content_str = "" 
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(LLM_API_URL, headers=headers, json=payload, timeout=20.0)
            
            if response.status_code != 200:
                log.error(f"LLM API ({LLM_PROVIDER}) request failed with status {response.status_code}: {response.text}")
                return None
            
            response_json = response.json()

            if LLM_PROVIDER == "google":
                content_str = _extract_google_response(response_json)
            else: 
                content_str = _extract_openai_response(response_json)
            
            # --- ✅ (Point 4) مرشح Non-JSON ---
            # ابحث عن أول '{' وآخر '}'
            json_match = re.search(r'\{.*\}', content_str, re.DOTALL)
            if not json_match:
                log.warning(f"LLM did not return a valid JSON block. Response: {content_str[:200]}")
                return None
            content_str = json_match.group(0)
            # --- نهاية المرشح ---
            
            parsed_data = json.loads(content_str)
            
            # --- ✅ (Point 2) تصنيف الأخطاء ---
            if "error" in parsed_data:
                reason = parsed_data.get("error", "Unknown LLM-side error")
                error_type = "other"
                if "Financial validation" in reason: error_type = "financial"
                elif "Missing required" in reason: error_type = "missing_fields"
                
                log.warning("LLMValidationFailure", extra={**log_meta, "error_type": error_type, "detail": reason})
                return None

            required_keys = ["asset", "side", "entry", "stop_loss", "targets"]
            if not all(k in parsed_data for k in required_keys):
                log.warning("LLMParseFailure: MissingKeys", extra={**log_meta, "missing_keys": [k for k in required_keys if k not in parsed_data]})
                return None
            
            # --- ✅ (Point 1) التحقق المالي الصارم ---
            if not _financial_consistency_check(parsed_data):
                log.warning("LLMParseFailure: FinancialConsistency", extra={**log_meta, "data": parsed_data, "error_type": "financial"})
                return None # ارفض البيانات غير المنطقية

            # --- ✅ (Point 5) سجل الميتاداتا للنجاح ---
            log.info("LLMParseSuccess", extra={
                **log_meta,
                "asset": parsed_data.get("asset"),
                "side": parsed_data.get("side"),
                "num_targets": len(parsed_data.get("targets", [])),
                "token_count_estimate": len(content_str) # تقدير تقريبي
            })
            
            return parsed_data

    except httpx.RequestError as e:
        log.error(f"HTTP error while calling LLM API ({LLM_PROVIDER}): {e}")
        return None
    except (json.JSONDecodeError, KeyError, TypeError, IndexError, ValueError) as e:
        log.error(f"Failed to parse LLM JSON response ({LLM_PROVIDER}): {e}. Response snippet: {content_str[:200]}")
        return None
    except Exception as e:
        log.error(f"Unexpected error in LLM parser ({LLM_PROVIDER}): {e}", exc_info=True)
        return None