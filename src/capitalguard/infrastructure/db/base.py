
#--- START OF FILE: src/capitalguard/infrastructure/db/models/__init__.py ---
from .base import Base
from .recommendation import RecommendationORM
from .auth import User, Role, UserRole

# This makes `Base.metadata` aware of all models for Alembic
__all__ = ["Base", "RecommendationORM", "User", "Role", "UserRole"]
--- END OF FILE ---```

---

### **المرحلة الثالثة: استبدال الملفات المتأثرة**

**التعليمات:**
استبدل المحتوى الكامل للملفات التالية بالنسخ المحدثة أدناه.

##### **1. `src/capitalguard/infrastructure/db/base.py`**
```python
--- START OF FILE: src/capitalguard/infrastructure/db/base.py ---
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from capitalguard.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

def get_session():
    """
    Dependency for FastAPI endpoints to get a DB session.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()