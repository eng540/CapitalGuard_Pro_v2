# ai_service/services/llm_parser.py
"""
خدمة الاتصال بـ LLM (v2.4.0 - Financial Validation Prompt).
✅ مُحسَّن: تم تحديث SYSTEM_PROMPT (v2.4) بشكل جذري.
✅ يضيف الآن قواعد التحقق المالي (SL vs Entry) *داخل* الموجه.
✅ يرفض LLM الآن إنشاء JSON إذا كانت البيانات غير منطقية ماليًا.
"""

import os
import httpx
import logging
import json
from typing import Dict, Any, Optional

log = logging.getLogger(__name__)

# --- قراءة جميع متغيرات البيئة ---
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "google").lower()
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_API_URL = os.getenv("LLM_API_URL")
LLM_MODEL = os.getenv("LLM_MODEL") 

if not LLM_API_KEY or not LLM_API_URL or not LLM_MODEL:
    log.warning("LLM environment variables (KEY, URL, MODEL) are not fully set. LLM parser will be disabled.")

# --- ✅ UPDATED: موجه النظام الموحد (v2.4) ---
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
3.  Entry: Find "Entry", "مناطق الدخول". Use the *first* number found after this key.
4.  Stop Loss: Find "SL", "ايقاف خسارة".
5.  Targets: Find "Targets", "TPs", "الاهداف". Extract *all* numbers that follow. Ignore ranges. Ignore emojis (✅). If no targets are found, validation rule #1 fails.
6.  Percentages: Extract "close_percent" if available (e.g., "@50%" or "(25% each)"). Default to 0.0.
7.  Notes: Add any extra text (like "Lev - 5x" or "لللحماية") to the "notes" field.
8.  Market/Order: Default to "Futures" and "LIMIT".

--- EXAMPLE (Arabic) ---
USER TEXT:
#ETH
Long
مناطق الدخول
3200
الاهداف
3500
3650
3900
4000
ايقاف خسارة 3800 للحماية

YOUR JSON RESPONSE:
{"error": "Financial validation failed (e.g., SL vs Entry)."}
---

--- EXAMPLE 2 (Valid Arabic) ---
USER TEXT:
#ETH
Long
مناطق الدخول
3200
الاهداف
3500
3650
3900
4000
ايقاف خسارة 3100 للحماية

YOUR JSON RESPONSE:
{
  "asset": "ETHUSDT",
  "side": "LONG",
  "entry": "3200",
  "stop_loss": "3100",
  "targets": [
    {"price": "3500", "close_percent": 0.0},
    {"price": "3650", "close_percent": 0.0},
    {"price": "3900", "close_percent": 0.0},
    {"price": "4000", "close_percent": 0.0}
  ],
  "market": "Futures",
  "order_type": "LIMIT",
  "notes": "للحماية"
}
---

The user's text will be provided next. Respond ONLY with the JSON object.
"""

# --- (باقي الملف ..._build_google_headers, _build_openai_headers, etc... يبقى كما هو) ---

# --- (الدالة الرئيسية) ---
async def parse_with_llm(text: str) -> Optional[Dict[str, Any]]:
    if not all([LLM_API_KEY, LLM_API_URL, LLM_MODEL]):
        log.debug("LLM parsing skipped: Environment variables incomplete.")
        return None

    headers: Dict[str, str]
    payload: Dict[str, Any]

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
            
            content_str = content_str.strip().removeprefix("```json").removesuffix("```").strip()
            parsed_data = json.loads(content_str)
            
            # --- ✅ فحص التحقق المحسّن ---
            
            # 1. التحقق من وجود خطأ صريح من الـ LLM (بناءً على الموجه الجديد)
            if "error" in parsed_data:
                log.warning(f"LLM returned a validation error: {parsed_data['error']}")
                return None # فشل التحليل عن قصد

            # 2. التحقق من وجود المفاتيح المطلوبة
            required_keys = ["asset", "side", "entry", "stop_loss", "targets"]
            if not all(k in parsed_data for k in required_keys):
                log.warning(f"LLM returned incomplete data (Missing keys): {parsed_data}")
                return None
            
            # 3. التحقق من أن الأهداف ليست فارغة
            if not parsed_data["targets"]:
                log.warning(f"LLM returned 0 targets for text: {text[:50]}... JSON: {content_str}")
                return None 

            log.info(f"LLM ({LLM_PROVIDER}) parsing successful for text snippet: {text[:50]}...")
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

# --- (باقي الدوال المساعدة لـ Google/OpenAI تبقى كما هي) ---
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