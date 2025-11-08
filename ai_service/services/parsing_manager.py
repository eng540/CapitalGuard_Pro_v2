# ai_service/services/parsing_manager.py
"""
المنسق (Orchestrator) لعملية التحليل (v1.2 - Validation Hotfix).
✅ HOTFIX: تم تعديل فحص `all()` للتحقق من *وجود* المفتاح
(key existence) بدلاً من قيمة الحقيقة (truthiness).
هذا يمنع الفشل إذا أرجع LLM قائمة `targets: []` فارغة.
"""

import logging
import time
from typing import Dict, Any, Optional
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy import select, update

# استيراد النماذج وقاعدة البيانات المحلية
from database import session_scope
from models import ParsingAttempt, ParsingTemplate, User
# استيراد المحللات
from services import regex_parser
from services import llm_parser

log = logging.getLogger(__name__)

# --- دوال مساعدة لتحويل البيانات ---

def _serialize_data_for_db(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    يحول البيانات المهيكلة (التي قد تحتوي على Decimal) إلى تنسيق JSON آمن
    لقاعدة البيانات (باستخدام نصوص للأرقام).
    """
    if not data:
        return {}
    
    entry = str(data.get("entry", "0"))
    stop_loss = str(data.get("stop_loss", "0"))
    targets = [
        {
            "price": str(t.get("price", "0")),
            "close_percent": t.get("close_percent", 0.0)
        } for t in data.get("targets", [])
    ]
    
    return {
        "asset": data.get("asset"),
        "side": data.get("side"),
        "entry": entry,
        "stop_loss": stop_loss,
        "targets": targets,
        "market": data.get("market", "Futures"),
        "order_type": data.get("order_type", "LIMIT"),
        "notes": data.get("notes")
    }

def _serialize_data_for_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    يحول البيانات المهيكلة إلى تنسيق الاستجابة (API Response).
    """
    return _serialize_data_for_db(data)


# --- الخدمة الأساسية ---

class ParsingManager:
    """
    يدير دورة حياة تحليل التوصية بالكامل.
    """
    
    def __init__(self, text: str, user_id: int):
        self.text = text
        self.user_id = user_id
        self.start_time = time.monotonic()
        self.attempt_id: Optional[int] = None
        self.parser_path_used: str = "failed"
        self.template_id_used: Optional[int] = None
        self.parsed_data: Optional[Dict[str, Any]] = None

    def _create_initial_attempt(self, session: Session) -> Optional[int]:
        """
        الخطوة 1: إنشاء سجل محاولة أولي.
        """
        try:
            user = session.get(User, self.user_id)
            if not user:
                log.error(f"User ID {self.user_id} not found in 'users' table. Cannot create attempt.")
                return None

            attempt = ParsingAttempt(
                user_id=self.user_id,
                raw_content=self.text,
                was_successful=False,
                parser_path_used="pending"
            )
            session.add(attempt)
            session.flush() # الحصول على الـ ID فورًا
            log.info(f"Created ParsingAttempt ID: {attempt.id} for user {self.user_id}")
            return attempt.id
        except Exception as e:
            log.critical(f"Failed to create initial ParsingAttempt in DB: {e}", exc_info=True)
            session.rollback()
            return None

    def _update_final_attempt(self, session: Session):
        """
        الخطوة 4: تحديث سجل المحاولة بالنتيجة النهائية.
        """
        if not self.attempt_id:
            log.error("Cannot update final attempt: attempt_id is None.")
            return

        try:
            latency_ms = int((time.monotonic() - self.start_time) * 1000)
            
            result_data_json = _serialize_data_for_db(self.parsed_data) if self.parsed_data else None

            stmt = (
                update(ParsingAttempt)
                .where(ParsingAttempt.id == self.attempt_id)
                .values(
                    was_successful= (self.parsed_data is not None),
                    result_data=result_data_json,
                    used_template_id=self.template_id_used,
                    parser_path_used=self.parser_path_used,
                    latency_ms=latency_ms
                )
            )
            session.execute(stmt)
            log.info(f"Updated ParsingAttempt ID: {self.attempt_id} with status: {self.parser_path_used}")
        except Exception as e:
            log.error(f"Failed to update final ParsingAttempt ID {self.attempt_id}: {e}", exc_info=True)
            session.rollback()

    async def analyze(self) -> Dict[str, Any]:
        """
        التنفيذ الكامل لعملية التحليل.
        """
        with session_scope() as session:
            self.attempt_id = self._create_initial_attempt(session)
            if not self.attempt_id:
                return {
                    "status": "error",
                    "error": "Failed to initialize parsing attempt (DB error)."
                }

        # --- الخطوة 2: المسار السريع (Regex) ---
        try:
            with session_scope() as regex_session:
                regex_result = regex_parser.parse_with_regex(self.text, regex_session)
            
            required_keys = ['asset', 'side', 'entry', 'stop_loss', 'targets']
            
            # ✅ HOTFIX (v1.1): Check for *key existence* and that *targets is not empty*
            if regex_result and all(k in regex_result for k in required_keys) and regex_result.get('targets'):
                log.info(f"Regex parser succeeded and found all required keys for attempt {self.attempt_id}.")
                self.parser_path_used = "regex"
                self.parsed_data = regex_result
            elif regex_result:
                log.warning(f"Regex parser result for attempt {self.attempt_id} was incomplete. Discarding and falling back to LLM.")
                self.parsed_data = None
            else:
                self.parsed_data = None
                
        except Exception as e:
            log.error(f"Regex parser failed unexpectedly: {e}", exc_info=True)
            self.parsed_data = None

        # --- الخطوة 3: المسار الذكي (LLM) ---
        if not self.parsed_data:
            log.info(f"Attempt {self.attempt_id}: Regex failed or incomplete, falling back to LLM.")
            try:
                llm_result = await llm_parser.parse_with_llm(self.text)
                if llm_result:
                    # ✅ HOTFIX (v1.2): Check for *key existence*, not truthiness.
                    # This allows `targets: []` to be a valid (though maybe undesirable) result.
                    if all(k in llm_result for k in required_keys):
                        
                        # ✅ HOTFIX (v1.1) Check: Did LLM also fail to find targets?
                        if not llm_result.get("targets"):
                             log.warning(f"LLM result for attempt {self.attempt_id} returned 0 targets. Failing.")
                             self.parser_path_used = "failed"
                             self.parsed_data = None
                        else:
                            self.parser_path_used = "llm"
                            self.parsed_data = llm_result
                    else:
                        log.error(f"LLM result for attempt {self.attempt_id} was incomplete (missing keys). Failing.")
                        self.parser_path_used = "failed"
                        self.parsed_data = None
            except Exception as e:
                log.error(f"LLM parser failed unexpectedly: {e}", exc_info=True)
                self.parser_path_used = "failed"
                self.parsed_data = None

        if not self.parsed_data:
            self.parser_path_used = "failed"

        # --- الخطوة 4: التحديث النهائي والرد ---
        with session_scope() as update_session:
            self._update_final_attempt(update_session)

        if self.parsed_data:
            return {
                "status": "success",
                "data": _serialize_data_for_response(self.parsed_data),
                "attempt_id": self.attempt_id,
                "parser_path_used": self.parser_path_used
            }
        else:
            return {
                "status": "error",
                "error": "Could not recognize a valid trade signal.",
                "attempt_id": self.attempt_id,
                "parser_path_used": "failed"
            }