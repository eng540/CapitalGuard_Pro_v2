# ai_service/schemas.py
"""
نماذج Pydantic (Schemas) للتحقق من صحة مدخلات ومخرجات واجهة برمجة التطبيقات (API)
لخدمة ai_service.

✅ v1.1.0 (ADR-003): Added ImageParseRequest model.
"""

from pydantic import BaseModel, Field, HttpUrl
from typing import List, Optional, Dict, Any, Union

# --- نماذج الإدخال (Request Bodies) ---

class ParseRequest(BaseModel):
    """
    النموذج المتوقع للطلب القادم إلى /ai/parse (تحليل نصي)
    """
    text: str = Field(..., min_length=10, description="النص الخام للتوصية المعاد توجيهها")
    user_id: int = Field(..., description="المعرف الداخلي (DB ID) للمستخدم الذي قام بإعادة التوجيه")

# ✅ NEW (ADR-003): النموذج المتوقع للطلب القادم إلى /ai/parse_image
class ImageParseRequest(BaseModel):
    """
    النموذج المتوقع للطلب القادم إلى /ai/parse_image (تحليل صور)
    """
    user_id: int = Field(..., description="المعرف الداخلي (DB ID) للمستخدم الذي قام بالرفع")
    image_url: HttpUrl = Field(..., description="رابط URL العام والمؤقت لصورة التوصية")


class CorrectionRequest(BaseModel):
    """
    النموذج المتوقع للطلب القادم إلى /ai/record_correction
    """
    attempt_id: int
    # البيانات هنا هي JSON (نصوص) لأنها تأتي من النظام الرئيسي
    original_data: Dict[str, Any]
    corrected_data: Dict[str, Any]

class TemplateSuggestRequest(BaseModel):
    """
    النموذج المتوقع للطلب القادم إلى /ai/suggest_template
    """
    attempt_id: int
    user_id: int

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
    attempt_id: Optional[int] = None
    parser_path_used: Optional[str] = None # 'regex', 'llm', 'vision', 'failed'
    error: Optional[str] = None

class CorrectionResponse(BaseModel):
    """
    الرد القياسي لنقطة النهاية /ai/record_correction
    """
    success: bool
    attempt_id: int
    message: Optional[str] = None

class TemplateSuggestResponse(BaseModel):
    """
    الرد القياسي لنقطة النهاية /ai/suggest_template
    """
    success: bool
    template_id: Optional[int] = None
    message: Optional[str] = None