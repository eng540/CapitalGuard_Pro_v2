# ai_service/services/llm_parser.py
"""
خدمة الاتصال بـ LLM (v2.3.0 - Robust Prompting).
✅ مُحسَّن: تم تحديث SYSTEM_PROMPT (v2.3) ليكون شديد الصرامة.
✅ إضافة أمثلة معقدة (multi-entry, ranges, text noise)
✅ إضافة منطق تنظيف (trimming) لـ `content_str` لإزالة ```json
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

# --- ✅ UPDATED: موجه النظام الموحد (v2.3) ---
SYSTEM_PROMPT = """
You are an expert financial analyst. Your task is to extract structured data from a forwarded trade signal.
You must analyze the user's text and return ONLY a valid JSON object.

RULES:
1.  Asset: Find the asset (e.g., "#ETH" -> "ETHUSDT", "#TURTLE" -> "TURTLEUSDT").
2.  Side: "LONG" or "SHORT".
3.  Entry: Find "Entry", "مناطق الدخول". If it's a range ("Entry 1", "Entry 2"), use *ONLY THE FIRST* price. ("Entry 1 - 0.099" -> "0.099").
4.  Stop Loss: Find "SL", "ايقاف خسارة".
5.  Targets: Find "Targets", "TPs", "الاهداف". Extract *all* numbers that follow. Ignore ranges like "0.105-0.112". Extract the individual numbers ("0.105", "0.112", "0.12", "0.15"). Ignore emojis (✅). If no targets are found, return an empty list [].
6.  Percentages: If targets have percentages (e.g., "0.15 (25% each)" or "105k@100"), extract them as "close_percent". Default to 0.0 if not specified.
7.  Notes: Add any extra text (like "Lev - 5x" or "لللحماية") to the "notes" field.
8.  Market/Order: Default to "Futures" and "LIMIT".

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
ايقاف خسارة 3800 للحماية
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
    {"price": "4000", "close_percent": 0.0}
  ],
  "market": "Futures",
  "order_type": "LIMIT",
  "notes": "لللحماية الرافعة المالية 10X-20X"
}
--- EXAMPLE 2 (Complex English) ---
USER TEXT:
Long #TURTLE
Lev - 5x
Entry 1 - 0.099 (50%) CMP
Entry 2 - 0.090 (50%)
TPs - 0.105 - 0.112 - 0.12 - 0.15 (25% each)
SL - $0.084
@CryptoSignals

YOUR JSON RESPONSE:
{
  "asset": "TURTLEUSDT",
  "side": "LONG",
  "entry": "0.099",
  "stop_loss": "0.084",
  "targets": [
    {"price": "0.105", "close_percent": 25.0},
    {"price": "0.112", "close_percent": 25.0},
    {"price": "0.12", "close_percent": 25.0},
    {"price": "0.15", "close_percent": 25.0}
  ],
  "market": "Futures",
  "order_type": "LIMIT",
  "notes": "Lev - 5x. Entry 2 - 0.090 (50%). @CryptoSignals"
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
            
            # ✅ HOTFIX: تنظيف الرد قبل تحليل JSON
            content_str = content_str.strip().removeprefix("```json").removesuffix("```").strip()
            
            parsed_data = json.loads(content_str)
            
            required_keys = ["asset", "side", "entry", "stop_loss", "targets"]
            if not all(k in parsed_data for k in required_keys):
                log.warning(f"LLM returned incomplete data (Missing keys): {parsed_data}")
                return None
            
            # (تم نقل فحص "الأهداف الفارغة" إلى parsing_manager)

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