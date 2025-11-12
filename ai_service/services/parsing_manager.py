--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/services/parsing_manager.py ---
# File: ai_service/services/parsing_manager.py
# Version: 3.0.0 (Decoupled)
# ✅ THE FIX: (Protocol 1) تم فصل الخدمة بالكامل عن قاعدة البيانات.
#    - إزالة جميع استدعاءات `session_scope` و `_create_initial_attempt` و `_update_final_attempt`.
#    - إزالة جميع نماذج ORM (مثل `ParsingAttempt`) والمنطق المتعلق بها.
#    - الدوال `analyze` و `analyze_image` تعيد الآن قاموس (dict) بالنتيجة مباشرة.
# 🎯 IMPACT: هذه الخدمة أصبحت "خدمة تحليل نقية" (Pure Parsing Service) ومعزولة تمامًا.

import logging
import time
from typing import Dict, Any, Optional
from decimal import Decimal

# ❌ REMOVED DB IMPORTS
# from database import session_scope
# from models import ParsingAttempt, ParsingTemplate, User

# استيراد المحللات
from services import regex_parser
from services import llm_parser
from services import image_parser

log = logging.getLogger(__name__)

# --- الخدمة الأساسية ---

class ParsingManager:
    """
    (v3.0 - Decoupled)
    يدير دورة حياة تحليل التوصية (بدون اتصال بقاعدة البيانات).
    """

    def __init__(self, user_id: int, text: Optional[str] = None, image_url: Optional[str] = None):
        self.text = text or ""
        self.image_url = image_url or ""
        self.user_id = user_id
        self.start_time = time.monotonic()
        # ❌ REMOVED DB STATE
        # self.attempt_id: Optional[int] = None
        self.parser_path_used: str = "failed"
        self.template_id_used: Optional[int] = None
        self.parsed_data: Optional[Dict[str, Any]] = None

    # ❌ REMOVED: _create_initial_attempt (DB logic)
    # ❌ REMOVED: _update_final_attempt (DB logic)

    async def analyze(self) -> Dict[str, Any]:
        """
        التنفيذ الكامل لعملية تحليل *النص*.
        Returns a dictionary with parsing results or error info.
        """
        
        # ❌ REMOVED: Initial DB attempt creation
        
        required_keys = ['asset', 'side', 'entry', 'stop_loss', 'targets']

        # --- الخطوة 1: المسار السريع (Regex) ---
        try:
            # ✅ REFACTORED: Regex parser no longer needs a session
            # We pass 'user_id' instead of 'session'
            regex_result = regex_parser.parse_with_regex(self.text, self.user_id) 
            
            if regex_result and all(k in regex_result for k in required_keys) and regex_result.get('targets'):
                log.info(f"Regex parser succeeded for user {self.user_id}.")
                self.parser_path_used = "regex"
                self.parsed_data = regex_result
            elif regex_result:
                log.warning(f"Regex parser result for user {self.user_id} was incomplete. Falling back to LLM.")
                self.parsed_data = None
            else:
                self.parsed_data = None
                
        except Exception as e:
            log.error(f"Regex parser failed unexpectedly: {e}", exc_info=True)
            self.parsed_data = None

        # --- الخطوة 2: المسار الذكي (LLM) ---
        if not self.parsed_data:
            log.info(f"User {self.user_id}: Regex failed, falling back to LLM.")
            try:
                llm_result = await llm_parser.parse_with_llm(self.text)
                if llm_result:
                    if all(k in llm_result for k in required_keys):
                        if not llm_result.get("targets"):
                             log.warning(f"LLM result for user {self.user_id} returned 0 targets. Failing.")
                             self.parser_path_used = "failed"
                             self.parsed_data = None
                        else:
                             self.parser_path_used = "llm"
                             self.parsed_data = llm_result
                    else:
                         log.error(f"LLM result for user {self.user_id} was incomplete (missing keys). Failing.")
                        self.parser_path_used = "failed"
                         self.parsed_data = None
            except Exception as e:
                log.error(f"LLM parser failed unexpectedly: {e}", exc_info=True)
                self.parser_path_used = "failed"
                self.parsed_data = None

        if not self.parsed_data:
            self.parser_path_used = "failed"

        # --- الخطوة 3: التحديث النهائي والرد ---
        # ❌ REMOVED: Final DB update
        
        latency_ms = int((time.monotonic() - self.start_time) * 1000)

        if self.parsed_data:
            return {
                "status": "success",
                # ✅ REFACTORED: Return raw data (with Decimals)
                "data": self.parsed_data,
                "parser_path_used": self.parser_path_used,
                "latency_ms": latency_ms
            }
        else:
            return {
                "status": "error",
                "error": "Could not recognize a valid trade signal.",
                "parser_path_used": "failed",
                "latency_ms": latency_ms
            }

    async def analyze_image(self) -> Dict[str, Any]:
        """
        التنفيذ الكامل لعملية تحليل *الصورة*.
        """
        # ❌ REMOVED: Initial DB attempt creation

        required_keys = ['asset', 'side', 'entry', 'stop_loss', 'targets']

        # --- الخطوة 1: المسار الذكي (Vision) ---
        log.info(f"User {self.user_id}: Starting Vision model parse.")
        try:
            vision_result = await image_parser.parse_with_vision(self.image_url)
            
            if vision_result:
                if all(k in vision_result for k in required_keys) and vision_result.get("targets"):
                    self.parser_path_used = "vision"
                    self.parsed_data = vision_result
                else:
                    log.error(f"Vision result for user {self.user_id} was incomplete. Failing.")
                    self.parser_path_used = "failed"
                    self.parsed_data = None
        except Exception as e:
            log.error(f"Vision parser failed unexpectedly: {e}", exc_info=True)
            self.parser_path_used = "failed"
            self.parsed_data = None

        if not self.parsed_data:
            self.parser_path_used = "failed"

        # --- الخطوة 2: التحديث النهائي والرد ---
        # ❌ REMOVED: Final DB update

        latency_ms = int((time.monotonic() - self.start_time) * 1000)

        if self.parsed_data:
            return {
                "status": "success",
                # ✅ REFACTORED: Return raw data (with Decimals)
                "data": self.parsed_data,
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
--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/services/parsing_manager.py ---