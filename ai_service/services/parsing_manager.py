#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/services/parsing_manager.py ---
# File: ai_service/services/parsing_manager.py
# Version: 3.1.0 (v5.1 Engine Refactor)
# ✅ THE FIX: (Protocol 1) تم تحديث هذا الملف ليتوافق مع v5.1.
#    - 1. (MAINTAIN) الحفاظ على منطق "الفصل" (Decoupled) - لا يوجد اتصال بقاعدة البيانات.
#    - 2. (NEW) هذا الملف يستدعي الآن `llm_parser` (v5.1) و `image_parser` (v5.1)
#       اللذين تم إصلاحهما بالكامل.
# 🎯 IMPACT: هذا يكمل ترقية `ai-service` إلى v5.1 Engine.

import logging
import time
from typing import Dict, Any, Optional
from decimal import Decimal

# ❌ REMOVED DB IMPORTS
# from database import session_scope
# from models import ParsingAttempt, ParsingTemplate, User

# استيراد المحللات (الآن v5.1)
from services import regex_parser # (ملاحظة: regex_parser لا يزال يستخدم DB)
from services import llm_parser
from services import image_parser

log = logging.getLogger(__name__)

# --- الخدمة الأساسية ---

class ParsingManager:
    """
    (v3.1.0 - Decoupled)
    يدير دورة حياة تحليل التوصية (بدون اتصال بقاعدة البيانات).
    """

    def __init__(self, user_id: int, text: Optional[str] = None, image_url: Optional[str] = None):
        self.text = text or ""
        self.image_url = image_url or ""
        self.user_id = user_id
        self.start_time = time.monotonic()
        self.parser_path_used: str = "failed"
        self.template_id_used: Optional[int] = None
        self.parsed_data: Optional[Dict[str, Any]] = None
        self.error_code: Optional[str] = None

    # ❌ REMOVED: _create_initial_attempt (DB logic)
    # ❌ REMOVED: _update_final_attempt (DB logic)

    async def analyze(self) -> Dict[str, Any]:
        """
        التنفيذ الكامل لعملية تحليل *النص*.
        Returns a dictionary with parsing results or error info.
        """
        
        required_keys = ['asset', 'side', 'entry', 'stop_loss', 'targets']

        # --- الخطوة 1: المسار السريع (Regex) ---
        # (ملاحظة: هذا المسار لا يزال يتطلب اتصال DB. إذا تم تعطيل DB، سيفشل هذا بهدوء)
        try:
            # ✅ REFACTORED: Regex parser no longer needs a session
            # We pass 'user_id' instead of 'session'
            regex_result = regex_parser.parse_with_regex(self.text, self.user_id) 
            
            if regex_result and all(k in regex_result for k in required_keys) and regex_result.get('targets'):
                log.info(f"Regex parser succeeded for user {self.user_id}.")
                self.parser_path_used = "regex"
                self.parsed_data = regex_result # (يحتوي على Decimals)
            elif regex_result:
                log.warning(f"Regex parser result for user {self.user_id} was incomplete. Falling back to LLM.")
                self.parsed_data = None
            else:
                self.parsed_data = None
                
        except Exception as e:
            log.error(f"Regex parser failed unexpectedly (maybe DB connection?): {e}", exc_info=True)
            self.parsed_data = None

        # --- الخطوة 2: المسار الذكي (LLM) ---
        if not self.parsed_data:
            log.info(f"User {self.user_id}: Regex failed, falling back to LLM.")
            try:
                llm_result = await llm_parser.parse_with_llm(self.text)
                if llm_result and llm_result.get("__error_code__"):
                    self.error_code = llm_result["__error_code__"]
                    self.parser_path_used = self.error_code
                    self.parsed_data = None
                elif llm_result:
                    if all(k in llm_result for k in required_keys):
                        if not llm_result.get("targets"):
                             log.warning(f"LLM result for user {self.user_id} returned 0 targets. Failing.")
                             self.parser_path_used = "failed"
                             self.parsed_data = None
                        else:
                             self.parser_path_used = "llm"
                             self.parsed_data = llm_result # (يحتوي على Decimals)
                    else:
                         log.error(f"LLM result for user {self.user_id} was incomplete (missing keys). Failing.")
                         self.parser_path_used = "failed"
                         self.parsed_data = None
                else:
                    self.parser_path_used = "failed"
                    self.parsed_data = None
            except Exception as e:
                log.error(f"LLM parser failed unexpectedly: {e}", exc_info=True)
                self.parser_path_used = "failed"
                self.parsed_data = None

        if not self.parsed_data:
            self.parser_path_used = "failed"

        # --- الخطوة 3: التحديث النهائي والرد ---
        latency_ms = int((time.monotonic() - self.start_time) * 1000)

        if self.parsed_data:
            return {
                "status": "success",
                "data": self.parsed_data, # (يحتوي على Decimals)
                "parser_path_used": self.parser_path_used,
                "latency_ms": latency_ms,
                "error_code": self.error_code,
            }
        else:
            return {
                "status": "error",
                "error": (
                    "AI provider is temporarily rate-limited; retry later."
                    if self.error_code == "provider_rate_limited"
                    else "AI provider routes are unavailable; retry later."
                    if self.error_code == "provider_unavailable"
                    else "Could not recognize a valid trade signal."
                ),
                "parser_path_used": self.parser_path_used,
                "latency_ms": latency_ms,
                "error_code": self.error_code,
            }

    async def analyze_image(self) -> Dict[str, Any]:
        """
        التنفيذ الكامل لعملية تحليل *الصورة*.
        """
        required_keys = ['asset', 'side', 'entry', 'stop_loss', 'targets']

        # --- الخطوة 1: المسار الذكي (Vision) ---
        log.info(f"User {self.user_id}: Starting Vision model parse.")
        try:
            vision_result = await image_parser.parse_with_vision(self.image_url)
            
            if vision_result and vision_result.get("__error_code__"):
                self.error_code = vision_result["__error_code__"]
                self.parser_path_used = self.error_code
                self.parsed_data = None
            elif vision_result:
                if all(k in vision_result for k in required_keys) and vision_result.get("targets"):
                    self.parser_path_used = "vision"
                    self.parsed_data = vision_result # (يحتوي على Decimals)
                else:
                    log.error(f"Vision result for user {self.user_id} was incomplete. Failing.")
                    self.parser_path_used = "failed"
                    self.parsed_data = None
            else:
                self.parser_path_used = "failed"
                self.parsed_data = None
        except Exception as e:
            log.error(f"Vision parser failed unexpectedly: {e}", exc_info=True)
            self.parser_path_used = "failed"
            self.parsed_data = None

        if not self.parsed_data:
            self.parser_path_used = "failed"

        # --- الخطوة 2: التحديث النهائي والرد ---
        latency_ms = int((time.monotonic() - self.start_time) * 1000)

        if self.parsed_data:
            return {
                "status": "success",
                "data": self.parsed_data, # (يحتوي على Decimals)
                "parser_path_used": self.parser_path_used,
                "latency_ms": latency_ms
            }
        else:
            return {
                "status": "error",
                "error": "Could not recognize a valid trade signal from the image.",
                "parser_path_used": "failed",
                "latency_ms": latency_ms
            }
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/services/parsing_manager.py ---