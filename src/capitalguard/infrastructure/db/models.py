#--- START OF FILE: src/capitalguard/infrastructure/db/models.py ---
#--- START OF FILE: src/capitalguard/infrastructure/db/models.py ---
# ✅ تم التعديل: هذا الملف يعمل الآن كنقطة وصول مركزية لنماذج ORM.
# يقوم باستيرادها من وحداتها المخصصة لضمان وجود مصدر واحد للحقيقة ومنع التعارضات.

# استيراد النموذج الأساسي الذي ترث منه جميع النماذج الأخرى
from .models.base import Base

# استيراد نماذج المصادقة (المستخدمون والأدوار)
from .models.auth import User, Role, UserRole

# استيراد نموذج التوصيات الكامل والصحيح من موقعه المخصص
from .models.recommendation import RecommendationORM

# هذا السطر اختياري ولكنه ممارسة جيدة.
# يحدد بشكل صريح ما الذي يتم "تصديره" عندما يستخدم ملف آخر `from .models import *`
__all__ = [
    "Base",
    "RecommendationORM",
    "User",
    "Role",
    "UserRole",
]
#--- END OF FILE ---