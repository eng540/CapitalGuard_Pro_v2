# --- START OF FILE: src/capitalguard/infrastructure/db/models/__init__.py ---
from .base import Base

# استيرادات “آمنة” – لا تكسر إذا ملف ناقص
try:
    from .auth import User, Role, UserRole
except Exception:
    User = Role = UserRole = None

try:
    from .recommendation import RecommendationORM
except Exception:
    RecommendationORM = None

try:
    from .channel import Channel
except Exception:
    Channel = None

try:
    from .published_message import PublishedMessage
except Exception:
    PublishedMessage = None

# الاستيراد الجديد – اجعله اختياريًا
try:
    from .recommendation_event import RecommendationEvent
except Exception:
    RecommendationEvent = None

__all__ = ["Base"]
for _name in ("User", "Role", "UserRole", "RecommendationORM", "Channel", "PublishedMessage", "RecommendationEvent"):
    if globals().get(_name) is not None:
        __all__.append(_name)
# --- END OF FILE ---