# ai_service/services/llm_parser.py
"""
خدمة الاتصال بـ LLM (مثل OpenAI/Gemini/OpenRouter) لتحليل النصوص.
هذا هو "المسار الذكي" (Smart Path) عندما يفشل Regex.
"""

import os
import httpx
import logging
from typing import Dict, Any, Optional

log = logging.getLogger(__name__)

# اقرأ المفتاح ونقطة النهاية من متغيرات البيئة
# استخدم OpenRouter كمثال افتراضي لمرونته
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_API_URL = os.getenv("LLM_API_URL", "https://api.openrouter.ai/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-3.5-turbo") # نموذج افتراضي

if not LLM_API_KEY:
    log.warning("LLM_API_KEY not set. LLM parser will be disabled.")

# هذا هو "الموجه الهندسي" (Engineered Prompt) الحاسم
# إنه يرشد الذكاء الاصطناعي لإرجاع JSON بالتنسيق الذي نريده بالضبط
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
The user's text will be provided next. Respond ONLY with the JSON object.
"""

async def parse_with_llm(text: str) -> Optional[Dict[str, Any]]:
    """
    يستدعي واجهة برمجة تطبيقات LLM لتحليل النص.
    يعيد قاموسًا (dict) بالبيانات المهيكلة أو None عند الفشل.
    """
    if not LLM_API_KEY:
        log.debug("LLM parsing skipped: API key not configured.")
        return None

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text}
        ],
        "response_format": {"type": "json_object"} # طلب JSON صريح إن أمكن
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(LLM_API_URL, headers=headers, json=payload, timeout=15.0)
            
            if response.status_code != 200:
                log.error(f"LLM API request failed with status {response.status_code}: {response.text}")
                return None
            
            response_json = response.json()
            content_str = response_json["choices"][0]["message"]["content"]
            
            # محاولة تحليل الـ JSON من رد الـ LLM
            import json
            parsed_data = json.loads(content_str)
            
            # التحقق الأساسي من صحة البيانات المستخرجة
            if not all(k in parsed_data for k in ["asset", "side", "entry", "stop_loss", "targets"]):
                log.warning(f"LLM returned incomplete data: {parsed_data}")
                return None
                
            log.info(f"LLM parsing successful for text snippet: {text[:50]}...")
            return parsed_data

    except httpx.RequestError as e:
        log.error(f"HTTP error while calling LLM API: {e}")
        return None
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log.error(f"Failed to parse LLM JSON response: {e}. Response was: {content_str[:200]}")
        return None
    except Exception as e:
        log.error(f"Unexpected error in LLM parser: {e}", exc_info=True)
        return None