# ai_service/services/llm_parser.py
"""
خدمة الاتصال بـ LLM (v2.2.0 - Arabic Prompt Hotfix).
✅ مُحسَّن: تم تحديث SYSTEM_PROMPT بشكل كبير.
✅ تم تعديل التعليمات لتكون أكثر صرامة بشأن استخراج الأرقام بعد الكلمات الرئيسية العربية (الاهداف، دخول).
✅ إضافة تعليمات صريحة لتجاهل الرموز التعبيرية (✅).
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

# --- ✅ UPDATED: موجه النظام الموحد (v2.2) ---
SYSTEM_PROMPT = """
You are an expert financial analyst. Your task is to extract structured data from a forwarded trade signal.
Analyze the user's text and return ONLY a valid JSON object with the following keys:
- "asset": string (e.g., "#ETH" -> "ETHUSDT")
- "side": string ("LONG" or "SHORT". "Long" -> "LONG")
- "entry": string (Look for "Entry", "مناطق الدخول". Use the *first* number found after this key. "مناطق الدخول 3875" -> "3875")
- "stop_loss": string (Look for "SL", "ايقاف خسارة". "ايقاف خسارة 3800" -> "3800")
- "targets": list[dict] (Look for "Targets", "TP", "الاهداف". Extract *all* numbers that follow this key, even if they are on new lines or have emojis. "الاهداف 3900✅ 3950 4000" -> [{"price": "3900", "close_percent": 0.0}, {"price": "3950", "close_percent": 0.0}, {"price": "4000", "close_percent": 0.0}]. If no targets are found, return an empty list [].)
- "market": string (Default to "Futures")
- "order_type": string (Default to "LIMIT")
- "notes": string (Any extra text or notes, like "10X-20X" or "لللحماية")

--- EXAMPLE 1 (Arabic) ---
USER TEXT:
#ETH
Long
مناطق الدخول
3875
الاهداف
3900✅
3950✅
4000
4300
4600
ايقاف خسارة 3800 لللحماية
الرافعة المالية 10X-20X

YOUR JSON RESPONSE:
{
  "asset": "ETHUSDT",
  "side": "LONG",
  "entry": "3875",
  "stop_loss": "3800",
  "targets": [
    {"price": "3900", "close_percent": 0.0},
    {"price": "3950", "close_percent": 0.0},
    {"price": "4000", "close_percent": 0.0},
    {"price": "4300", "close_percent": 0.0},
    {"price": "4600", "close_percent": 0.0}
  ],
  "market": "Futures",
  "order_type": "LIMIT",
  "notes": "لللحماية الرافعة المالية 10X-20X"
}
---

The user's text will be provided next. Respond ONLY with the JSON object.
"""

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
    يستدعي واجهة برمجة تطبيقات LLM المناسبة بناءً
    على LLM_PROVIDER.
    """
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
            log.error(f"Unsupported LLM_PROVIDER: {LLM_PROVIDER}. Supported: 'google', 'openai', 'openrouter'.")
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
            
            content_str = content_str.strip().removeprefix("```json").removesuffix("```")
            parsed_data = json.loads(content_str)
            
            # --- ✅ فحص التحقق المحسّن ---
            required_keys = ["asset", "side", "entry", "stop_loss", "targets"]
            if not all(k in parsed_data for k in required_keys):
                log.warning(f"LLM returned incomplete data (Missing keys): {parsed_data}")
                return None
            
            # ✅ الفحص الحاسم الذي طلبته
            if not parsed_data["targets"]:
                log.warning(f"LLM returned 0 targets for text: {text[:50]}... JSON: {content_str}")
                # هذا لا يزال يعتبر فشلًا في الاستخراج لأن النص يحتوي بوضوح على أهداف
                return None # ارفض هذا التحليل غير المكتمل

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