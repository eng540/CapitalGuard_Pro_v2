# ai_service/main.py
"""
(v1.2.0 - DetachedInstanceError Hotfix)
✅ HOTFIX: تم إصلاح خطأ sqlalchemy.orm.exc.DetachedInstanceError
في نقطة نهاية `suggest_template`.
✅ يتم الآن قراءة `attempt.id` و `template_id` وتخزينهما في متغيرات
محلية *قبل* إغلاق (commit) الجلسة.
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
    version="1.2.0",
    description="خدمة مستقلة لتحليل وتفسير توصيات التداول."
)

@app.on_event("startup")
async def startup_event():
    log.info("AI Parsing Service is starting up...")
    if not os.getenv("LLM_API_KEY"):
        log.warning("LLM_API_KEY is not set. LLM fallback will be disabled.")
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
    نقطة النهاية الرئيسية لتحليل نص التوصية.
    """
    log.info(f"Received parse request for user {request.user_id}, snippet: {request.text[:50]}...")
    try:
        manager = ParsingManager(text=request.text, user_id=request.user_id)
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
        log.error(f"Validation error during parsing: {e}")
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
            pattern_placeholder = (
                f"# REVIEW NEEDED: Source Attempt ID {attempt.id}\n"
                f"# User ID: {request.user_id}\n"
                f"# Corrections:\n{json.dumps(attempt.corrections_diff, indent=2)}\n\n"
                f"# --- Original Content ---\n{attempt.raw_content}"
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