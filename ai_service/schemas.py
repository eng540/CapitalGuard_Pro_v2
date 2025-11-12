#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/schemas.py ---
# File: ai_service/schemas.py
# Version: 2.0.0 (Decoupled)
# ✅ THE FIX: (Protocol 1) إزالة Schemas المتعلقة بقاعدة البيانات (Correction/Template).
#    - إزالة `attempt_id` من `ParseResponse`.
# 🎯 IMPACT: Schemas تعكس الآن خدمة تحليل نقية وعديمة الحالة.

from pydantic import BaseModel, Field, HttpUrl
from typing import List, Optional, Dict, Any, Union

# --- نماذج الإدخال (Request Bodies) ---

class ParseRequest(BaseModel):
    """
    النموذج المتوقع للطلب القادم إلى /ai/parse (تحليل نصي)
    """
    text: str = Field(..., min_length=10, description="النص الخام للتوصية المعاد توجيهها")
    user_id: int = Field(..., description="المعرف الداخلي (DB ID) للمستخدم الذي قام بإعادة التوجيه")

class ImageParseRequest(BaseModel):
    """
    النموذج المتوقع للطلب القادم إلى /ai/parse_image (تحليل صور)
    """
    user_id: int = Field(..., description="المعرف الداخلي (DB ID) للمستخدم الذي قام بالرفع")
    image_url: HttpUrl = Field(..., description="رابط URL العام والمؤقت لصورة التوصية")

# ❌ REMOVED: CorrectionRequest
# ❌ REMOVED: TemplateSuggestRequest

# --- نماذج المخرجات (Response Bodies) ---

class TargetResponse(BaseModel):
    """
    نموذج الهدف (Target) في الرد.
    يتم إرجاع الأسعار كنصوص (strings) لضمان الدقة عند عبور JSON.
    """
    price: str
    close_percent: float

class ParsedDataResponse(BaseModel):
    """
    البيانات المهيكلة التي يتم إرجاعها عند نجاح التحليل.
    """
    asset: str
    side: str
    entry: str
    stop_loss: str
    targets: List[TargetResponse]
    market: Optional[str] = "Futures"
    order_type: Optional[str] = "LIMIT"
    notes: Optional[str] = None

class ParseResponse(BaseModel):
    """
    الرد القياسي لنقطة النهاية /ai/parse أو /ai/parse_image
    """
    status: str # "success" or "error"
    data: Optional[ParsedDataResponse] = None
    # ❌ REMOVED: attempt_id
    parser_path_used: Optional[str] = None # 'regex', 'llm', 'vision', 'failed'
    error: Optional[str] = None

# ❌ REMOVED: CorrectionResponse
# ❌ REMOVED: TemplateSuggestResponse
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/schemas.py ---