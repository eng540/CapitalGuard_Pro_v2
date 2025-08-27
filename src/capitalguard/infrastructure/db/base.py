#--- START OF FILE: src/capitalguard/infrastructure/db/base.py ---
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from capitalguard.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
--- END OF FILE ---```

##### **2. `src/capitalguard/infrastructure/db/repository.py`**
*   **السبب:** تحديث استيراد `RecommendationORM` واستخدام `yield` في `get_session` بشكل صحيح.

```python
--- START OF FILE: src/capitalguard/infrastructure/db/repository.py ---
from typing import List, Optional
from sqlalchemy.orm import Session
from datetime import datetime

from capitalguard.domain.entities import Recommendation
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from .models import RecommendationORM, Base # ✅ Import Base and models from the new package
from .base import engine, get_session

# Create tables if not present (dev only). Use Alembic in prod.
Base.metadata.create_all(bind=engine)

class RecommendationRepository:
    def __init__(self, session: Optional[Session] = None) -> None:
        self._get_session = lambda: session if session else next(get_session())

    # ... (باقي محتوى الملف repository.py يبقى كما هو، فقط تأكد من أن `get_session` تُستخدم بشكل صحيح)
    def add(self, rec: Recommendation) -> Recommendation:
        with self._get_session() as s:
            row = RecommendationORM(
                asset=rec.asset.value,
                side=rec.side.value,
                entry=rec.entry.value,
                stop_loss=rec.stop_loss.value,
                targets=rec.targets.values,
                status=rec.status,
                channel_id=rec.channel_id,
                user_id=rec.user_id,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return self._to_entity(row)
    # ... (باقي الدوال مثل get, list_open, list_all, update يجب أن تستخدم `with self._get_session() as s:`)

    # هذا مجرد مثال على دالة get، يجب تحديث الباقي بنفس النمط
    def get(self, rec_id: int) -> Optional[Recommendation]:
        with self._get_session() as s:
            row = s.get(RecommendationORM, rec_id)
            return self._to_entity(row) if row else None

    # ... (Implement other methods like list_open, list_all, update similarly)
#--- END OF FILE ---