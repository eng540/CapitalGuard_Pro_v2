# ai_service/services/llm_parser.py
"""
خدمة الاتصال بـ LLM (v2.0.0 - متعدد المنصات).
✅ مرن: يقرأ LLM_PROVIDER (google, openai, openrouter) لاختيار المنطق الصحيح.
✅ لا يحتاج لتعديل الكود للتبديل بين المزودين.
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
LLM_MODEL = os.getenv("LLM_MODEL") # (e.g., "gemini-1.5-flash" or "gpt-3.5-turbo")

if not LLM_API_KEY or not LLM_API_URL or not LLM_MODEL:
    log.warning("LLM environment variables (KEY, URL, MODEL) are not fully set. LLM parser will be disabled.")

# --- موجه النظام الموحد ---
SYSTEM_PROMPT = """
You are an expert financial analyst. Your task is to extract structured data from a forwarded trade signal.
Analyze the user's text and return ONLY a valid JSON object with the following keys:
- "asset": string (e.g., "BTCUSDT", "ETHUSDT")
- "side": string ("LONG" or "SHORT")
- "entry": string (The first or primary entry price, as a number)
- "stop_loss": string (The stop loss price, as a number)
- "targets": list[dict] (A list of targets, e.g., [{"price": "61000", "close_percent": 0.0}, {"price": "62000", "close_percent": 50.0}])
- "market": string (Default to "Futures" if not specified)
- "order_type": string (Default to "LIMIT" if not specified)
- "notes": string (Any extra text or notes)

If a value is not found, omit the key or set it to null.
Respond ONLY with the JSON object.
"""

# --- المنطق الخاص بـ Google ---
def _build_google_headers(api_key: str) -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-goog-api-key": api_key
    }

def _build_google_payload(text: str) -> Dict[str, Any]:
    # موجه Gemini يتضمن "System Prompt" كجزء من المحتوى
    return {
        "contents": [
            {
                "parts": [
                    {"text": SYSTEM_PROMPT},
                    {"text": "--- USER TEXT START ---"},
                    {"text": text},
                    {"text": "--- USER TEXT END ---"}
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.0 
        }
    }

def _extract_google_response(response_json: Dict[str, Any]) -> str:
    # يستخرج النص الذي يحتوي على JSON من رد Gemini
    return response_json["candidates"][0]["content"]["parts"][0]["text"]

# --- المنطق الخاص بـ OpenAI / OpenRouter ---
def _build_openai_headers(api_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

def _build_openai_payload(text: str) -> Dict[str, Any]:
    # موجه OpenAI/OpenRouter يفصل بين النظام والمستخدم
    return {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text}
        ],
        "response_format": {"type": "json_object"} # طلب JSON صريح
    }

def _extract_openai_response(response_json: Dict[str, Any]) -> str:
    # يستخرج النص الذي يحتوي على JSON من رد OpenAI
    return response_json["choices"][0]["message"]["content"]


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

    try:
        # 1. بناء الطلب بناءً على المزود
        if LLM_PROVIDER == "google":
            headers = _build_google_headers(LLM_API_KEY)
            payload = _build_google_payload(text)
        
        elif LLM_PROVIDER in ("openai", "openrouter"):
            headers = _build_openai_headers(LLM_API_KEY)
            payload = _build_openai_payload(text)
        
        else:
            log.error(f"Unsupported LLM_PROVIDER: {LLM_PROVIDER}. Supported: 'google', 'openai', 'openrouter'.")
            return None
            
    except Exception as e:
        log.error(f"Failed to build LLM payload: {e}", exc_info=True)
        return None

    # 2. إرسال الطلب
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(LLM_API_URL, headers=headers, json=payload, timeout=20.0)
            
            if response.status_code != 200:
                log.error(f"LLM API ({LLM_PROVIDER}) request failed with status {response.status_code}: {response.text}")
                return None
            
            response_json = response.json()
            content_str: str

            # 3. استخراج الرد بناءً على المزود
            if LLM_PROVIDER == "google":
                content_str = _extract_google_response(response_json)
            else: # openai, openrouter
                content_str = _extract_openai_response(response_json)
            
            # 4. تحليل الـ JSON النهائي
            parsed_data = json.loads(content_str)
            
            if not all(k in parsed_data for k in ["asset", "side", "entry", "stop_loss", "targets"]):
                log.warning(f"LLM returned incomplete data: {parsed_data}")
                return None
                
            log.info(f"LLM ({LLM_PROVIDER}) parsing successful for text snippet: {text[:50]}...")
            return parsed_data

    except httpx.RequestError as e:
        log.error(f"HTTP error while calling LLM API ({LLM_PROVIDER}): {e}")
        return None
    except (json.JSONDecodeError, KeyError, TypeError, IndexError) as e:
        log.error(f"Failed to parse LLM JSON response ({LLM_PROVIDER}): {e}. Response snippet: {response.text[:200]}")
        return None
    except Exception as e:
        log.error(f"Unexpected error in LLM parser ({LLM_PROVIDER}): {e}", exc_info=True)
        return None