# ai_service/main.py
"""
(v2.0.0 - ADR-003 Image Parsing)
✅ NEW: Added the /ai/parse_image endpoint.
    - This endpoint accepts an `ImageParseRequest` (with a `image_url`).
    - It uses the `ParsingManager.analyze_image` method to orchestrate
      the vision model parsing.
    - It reuses the same `ParseResponse` model for a consistent API.
✅ HOTFIX (v1.2.0): Fixed sqlalchemy.orm.exc.DetachedInstanceError
    in the `suggest_template` endpoint.
"""

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
    ImageParseRequest, # ✅ NEW (ADR-003)
    CorrectionRequest, CorrectionResponse,
    TemplateSuggestRequest, TemplateSuggestResponse,
    ParsedDataResponse
)
from services.parsing_manager import ParsingManager
from database import session_scope
from models import ParsingAttempt, ParsingTemplate

# --- تهيئة التطبيق ---
app = FastAPI(
    title="CapitalGuard AI Parsing Service",
    version="2.0.0", # ✅ Version bump
    description="خدمة مستقلة لتحليل وتفسير توصيات التداول (نص وصور)."
)

@app.on_event("startup")
async def startup_event():
    log.info("AI Parsing Service is starting up...")
    if not os.getenv("LLM_API_KEY"):
        log.warning("LLM_API_KEY is not set. LLM/Vision fallback will be disabled.")
    if not os.getenv("DATABASE_URL"):
        log.critical("DATABASE_URL is not set. Service will not function.")
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
        result_dict = await manager.analyze()
        
        if result_dict.get("status") == "success":
            return ParseResponse(
                status="success",
                data=ParsedDataResponse(**result_dict.get("data")),
                attempt_id=result_dict.get("attempt_id"),
                parser_path_used=result_dict.get("parser_path_used")
            )
        else:
            return ParseResponse(
                status="error",
                error=result_dict.get("error", "Unknown error"),
                attempt_id=result_dict.get("attempt_id"),
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

# ✅ NEW (ADR-003): Endpoint for parsing images
@app.post("/ai/parse_image", response_model=ParseResponse)
async def parse_trade_image(request: ImageParseRequest):
    """
    نقطة النهاية الرئيسية لتحليل *الصورة*.
    """
    log.info(f"Received image parse request for user {request.user_id}, url: ...{str(request.image_url)[-50:]}")
    try:
        # Pydantic v2+ models: .image_url is a HttpUrl object, convert to str
        manager = ParsingManager(user_id=request.user_id, image_url=str(request.image_url))
        result_dict = await manager.analyze_image()
        
        if result_dict.get("status") == "success":
            return ParseResponse(
                status="success",
                data=ParsedDataResponse(**result_dict.get("data")),
                attempt_id=result_dict.get("attempt_id"),
                parser_path_used=result_dict.get("parser_path_used") # Should be 'vision'
            )
        else:
            return ParseResponse(
                status="error",
                error=result_dict.get("error", "Unknown error"),
                attempt_id=result_dict.get("attempt_id"),
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


@app.post("/ai/record_correction", response_model=CorrectionResponse)
async def record_correction(request: CorrectionRequest):
    """
    نقطة نهاية لتسجيل التصحيحات التي أجراها المستخدم.
    """
    log.info(f"Received correction request for attempt {request.attempt_id}")
    try:
        with session_scope() as session:
            attempt = session.get(ParsingAttempt, request.attempt_id)
            
            if not attempt:
                log.warning(f"Correction request for non-existent attempt {request.attempt_id}")
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attempt ID not found")
            
            diff = {
                "original": request.original_data,
                "corrected": request.corrected_data
            }
            
            attempt.was_corrected = True
            attempt.corrections_diff = diff
            
            session.add(attempt)
        
        return CorrectionResponse(success=True, attempt_id=request.attempt_id)

    except Exception as e:
        log.error(f"Failed to record correction for attempt {request.attempt_id}: {e}", exc_info=True)
        return CorrectionResponse(success=False, attempt_id=request.attempt_id, message=str(e))

@app.post("/ai/suggest_template", response_model=TemplateSuggestResponse)
async def suggest_template(request: TemplateSuggestRequest):
    """
    نقطة نهاية لإنشاء قالب جديد مقترح بناءً على تصحيح.
    """
    log.info(f"Received template suggestion request for attempt {request.attempt_id} from user {request.user_id}")
    template_id = None
    attempt_id_log = request.attempt_id # للتسجيل في حال فشل
    
    try:
        with session_scope() as session:
            attempt = session.get(ParsingAttempt, request.attempt_id)
            if not attempt:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attempt ID not found")
            
            if not attempt.was_corrected or attempt.user_id != request.user_id:
                log.warning(f"Invalid template suggestion for attempt {request.attempt_id}. Corrected: {attempt.was_corrected}, User: {attempt.user_id}")
                return TemplateSuggestResponse(success=False, message="Invalid suggestion request.")

            template_name = f"User {request.user_id} Suggestion (Attempt {attempt.id})"
            
            # Check if content is a URL (from image) or text
            raw_content_display = attempt.raw_content
            if raw_content_display.startswith("http"):
                raw_content_display = f"[Image URL: {raw_content_display}]"

            pattern_placeholder = (
                f"# REVIEW NEEDED: Source Attempt ID {attempt.id}\n"
                f"# User ID: {request.user_id}\n"
                f"# Corrections:\n{json.dumps(attempt.corrections_diff, indent=2)}\n\n"
                f"# --- Original Content ---\n{raw_content_display}"
            )

            new_template = ParsingTemplate(
                name=template_name,
                pattern_type="regex_review_needed",
                pattern_value=pattern_placeholder,
                analyst_id=request.user_id,
                is_public=False,
                stats={"source_attempt_id": attempt.id}
            )
            session.add(new_template)
            session.flush()
            
            # ✅ HOTFIX: اقرأ الـ ID *قبل* إغلاق الجلسة
            template_id = new_template.id
            attempt_id_log = attempt.id
        
        # ✅ HOTFIX: تم نقل التسجيل إلى *خارج* نطاق الجلسة
        log.info(f"Created new template (ID: {template_id}) for review from attempt {attempt_id_log}")
        return TemplateSuggestResponse(success=True, template_id=template_id)

    except Exception as e:
        log.error(f"Failed to suggest template for attempt {attempt_id_log}: {e}", exc_info=True)
        if isinstance(e, NameError) and 'json' in str(e):
            log.critical("FATAL: json module not imported in main.py")
        return TemplateSuggestResponse(success=False, message=str(e))