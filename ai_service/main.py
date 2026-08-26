#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/main.py ---
# File: ai_service/main.py
# Version: 3.1.0 (v5.1 Engine Refactor)
# ✅ THE FIX: (Protocol 1) تحديث للتعامل مع Decimals من v5.1 Engine.
#    - 1. (MAINTAIN) الحفاظ على "الفصل" (Decoupled) - لا يوجد اتصال بقاعدة البيانات.
#    - 2. (NEW) إضافة دالة `_serialize_data_for_response` لتحويل `Decimals`
#       التي يتم إرجاعها من `ParsingManager` إلى `strings` لـ JSON.
# 🎯 IMPACT: هذا الملف الآن يتوافق تمامًا مع مخرجات v5.1 Engine.

import logging
import os
import json
from typing import Dict, Any, Optional, List # ✅ ADDED
from decimal import Decimal # ✅ ADDED
from fastapi import FastAPI, Request, HTTPException, status
from pydantic import ValidationError

# إعداد التسجيل
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
# httpx/httpcore INFO logs can include full Telegram file URLs. Keep only
# warnings/errors; application telemetry performs its own redaction.
for _http_logger_name in ("httpx", "httpcore"):
    logging.getLogger(_http_logger_name).setLevel(logging.WARNING)
log = logging.getLogger(__name__)

# استيراد النماذج (Schemas) والمنسق (Manager)
from schemas import (
    ParseRequest, ParseResponse,
    ImageParseRequest,
    ParsedDataResponse
)
from services.parsing_manager import ParsingManager
from services.parsing_utils import redact_sensitive_url
from services.provider_router import get_provider_router, router_enabled
# ❌ REMOVED DB IMPORTS

# --- تهيئة التطبيق ---
app = FastAPI(
    title="CapitalGuard AI Parsing Service (Decoupled)",
    version="3.1.0", # ✅ Version bump
    description="خدمة مستقلة لتحليل وتفسير توصيات التداول (نص وصور) - بدون حالة DB."
)

@app.on_event("startup")
async def startup_event():
    log.info("AI Parsing Service (Decoupled) is starting up...")
    if not os.getenv("LLM_API_KEY"):
        log.warning("LLM_API_KEY is not set. Legacy LLM/Vision fallback will be disabled.")
    if router_enabled():
        log.info("AI provider router enabled routes=%s", get_provider_router().public_status())
    log.info("AI Service startup complete.")

# --- نقاط النهاية (Endpoints) ---

@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    """نقطة نهاية للتحقق من صحة الخدمة."""
    return {"status": "ok"}

# --- ✅ NEW (v3.1): Helper function to serialize Decimals ---
def _serialize_data_for_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    يحول البيانات المهيكلة (التي قد تحتوي على Decimal) إلى تنسيق الاستجابة (API Response).
    """
    if not data:
        return {}
    
    entry = data.get("entry")
    stop_loss = data.get("stop_loss")
    targets = data.get("targets", [])

    return {
        "asset": data.get("asset"),
        "side": data.get("side"),
        "entry": str(entry) if entry is not None else None,
        "stop_loss": str(stop_loss) if stop_loss is not None else None,
        "targets": [
            {
                "price": str(t.get("price")) if t.get("price") is not None else "0",
                "close_percent": t.get("close_percent", 0.0)
            } for t in targets
        ],
        "leverage": str(data.get("leverage")) if data.get("leverage") is not None else None,
        "market": data.get("market", "Futures"),
        "order_type": data.get("order_type", "LIMIT"),
        "notes": data.get("notes")
    }

@app.post("/ai/parse", response_model=ParseResponse)
async def parse_trade_text(request: ParseRequest):
    """
    نقطة النهاية الرئيسية لتحليل *النص*.
    """
    log.info(f"Received text parse request for user {request.user_id}, snippet: {request.text[:50]}...")
    try:
        manager = ParsingManager(user_id=request.user_id, text=request.text)
        result_dict = await manager.analyze()
        
        if result_dict.get("status") == "success":
            # ✅ (v3.1) Serialize Decimals to strings for the response
            serialized_data = _serialize_data_for_response(result_dict.get("data"))
            return ParseResponse(
                status="success",
                data=ParsedDataResponse(**serialized_data),
                parser_path_used=result_dict.get("parser_path_used")
            )
        else:
            if result_dict.get("error_code") in {"provider_rate_limited", "provider_unavailable"}:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=(
                        "AI provider is temporarily rate-limited; retry later."
                        if result_dict.get("error_code") == "provider_rate_limited"
                        else "AI provider routes are unavailable; retry later."
                    ),
                    headers={"Retry-After": "5"},
                )
            return ParseResponse(
                status="error",
                error=result_dict.get("error", "Unknown error"),
                parser_path_used=result_dict.get("parser_path_used")
            )

    except HTTPException:
        raise
    except ValidationError as e:
        log.error(f"Validation error during text parsing: {e}")
        return ParseResponse(
            status="error",
            error=f"Internal data validation error: {e}",
            parser_path_used="failed"
        )
    except Exception as e:
        log.critical(f"Unexpected error in /ai/parse endpoint: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected internal error occurred: {e}"
        )

@app.post("/ai/parse_image", response_model=ParseResponse)
async def parse_trade_image(request: ImageParseRequest):
    """
    نقطة النهاية الرئيسية لتحليل *الصورة*.
    """
    log.info("Received image parse request for user %s, source=%s", request.user_id, redact_sensitive_url(request.image_url).split("/file/")[0])
    try:
        manager = ParsingManager(user_id=request.user_id, image_url=str(request.image_url))
        result_dict = await manager.analyze_image()
        if result_dict.get("error_code") in {"provider_rate_limited", "provider_unavailable"}:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "AI provider is temporarily rate-limited; retry later."
                    if result_dict.get("error_code") == "provider_rate_limited"
                    else "AI provider routes are unavailable; retry later."
                ),
                headers={"Retry-After": "5"},
            )
        
        if result_dict.get("status") == "success":
            # ✅ (v3.1) Serialize Decimals to strings for the response
            serialized_data = _serialize_data_for_response(result_dict.get("data"))
            return ParseResponse(
                status="success",
                data=ParsedDataResponse(**serialized_data),
                parser_path_used=result_dict.get("parser_path_used")
            )
        else:
            return ParseResponse(
                status="error",
                error=result_dict.get("error", "Unknown error"),
                parser_path_used=result_dict.get("parser_path_used")
            )

    except HTTPException:
        raise
    except ValidationError as e:
        log.error(f"Validation error during image parsing: {e}")
        return ParseResponse(
            status="error",
            error=f"Internal data validation error: {e}",
            parser_path_used="failed"
        )
    except Exception as e:
        log.critical(f"Unexpected error in /ai/parse_image endpoint: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected internal error occurred: {e}"
        )
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/main.py ---