--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/main.py ---
# File: ai_service/main.py
# Version: 3.0.0 (Decoupled)
# ✅ THE FIX: (Protocol 1) تم فصل الخدمة بالكامل عن قاعدة البيانات.
#    - إزالة جميع واردات قاعدة البيانات (`session_scope`, `ParsingAttempt`, `ParsingTemplate`).
#    - إزالة عمليات التحقق من قاعدة البيانات عند بدء التشغيل (`startup` event).
#    - إزالة نقطة النهاية `/record_correction` (تم نقل المنطق إلى `api`).
#    - إزالة نقطة النهاية `/suggest_template` (تم نقل المنطق إلى `api`).
#    - تبسيط `/ai/parse` و `/ai/parse_image` لاستدعاء المدير (Manager) وإرجاع النتيجة فقط.
# 🎯 IMPACT: هذه الخدمة الآن "عديمة الحالة" (Stateless) فيما يتعلق بقاعدة البيانات.

import logging
import os
import json
from fastapi import FastAPI, Request, HTTPException, status
from pydantic import ValidationError

# إعداد التسجيل
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger(__name__)

# استيراد النماذج (Schemas) والمنسق (Manager)
from schemas import (
    ParseRequest, ParseResponse,
    ImageParseRequest,
    # ❌ REMOVED Correction/Template schemas
    ParsedDataResponse
)
from services.parsing_manager import ParsingManager
# ❌ REMOVED DB IMPORTS
# from database import session_scope
# from models import ParsingAttempt, ParsingTemplate

# --- تهيئة التطبيق ---
app = FastAPI(
    title="CapitalGuard AI Parsing Service (Decoupled)",
    version="3.0.0", # ✅ Version bump
    description="خدمة مستقلة لتحليل وتفسير توصيات التداول (نص وصور) - بدون حالة DB."
)

@app.on_event("startup")
async def startup_event():
    log.info("AI Parsing Service (Decoupled) is starting up...")
    if not os.getenv("LLM_API_KEY"):
        log.warning("LLM_API_KEY is not set. LLM/Vision fallback will be disabled.")
    # ❌ REMOVED DB check
    log.info("AI Service startup complete.")

# --- نقاط النهاية (Endpoints) ---

@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    """نقطة نهاية للتحقق من صحة الخدمة."""
    return {"status": "ok"}

@app.post("/ai/parse", response_model=ParseResponse)
async def parse_trade_text(request: ParseRequest):
    """
    نقطة النهاية الرئيسية لتحليل *النص*.
    """
    log.info(f"Received text parse request for user {request.user_id}, snippet: {request.text[:50]}...")
    try:
        manager = ParsingManager(user_id=request.user_id, text=request.text)
        # ✅ REFACTORED: Call manager directly, no DB session
        result_dict = await manager.analyze()
        
        # ✅ REFACTORED: تحويل بيانات Decimal إلى JSON متوافق
        if result_dict.get("status") == "success":
            # Pydantic v2+ uses .model_dump() to serialize, but our ParsedDataResponse
            # expects strings. We must serialize Decimals manually.
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
    log.info(f"Received image parse request for user {request.user_id}, url: ...{str(request.image_url)[-50:]}")
    try:
        manager = ParsingManager(user_id=request.user_id, image_url=str(request.image_url))
        # ✅ REFACTORED: Call manager directly, no DB session
        result_dict = await manager.analyze_image()
        
        if result_dict.get("status") == "success":
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

# --- ✅ ADDED: Helper function to serialize Decimals ---
def _serialize_data_for_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    يحول البيانات المهيكلة (التي قد تحتوي على Decimal) إلى تنسيق الاستجابة (API Response).
    (منسوخة من `parsing_manager` القديم)
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
        "market": data.get("market", "Futures"),
        "order_type": data.get("order_type", "LIMIT"),
        "notes": data.get("notes")
    }

# ❌ REMOVED: /ai/record_correction endpoint
# ❌ REMOVED: /ai/suggest_template endpoint
--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/main.py ---