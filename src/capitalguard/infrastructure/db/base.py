# File: src/capitalguard/infrastructure/db/base.py
# [STEP-1A] يستورد engine من uow.py بدلاً من إنشاء محرك مستقل
#
# التغيير:
#   قبل: كان ينشئ engine مستقلاً ثانياً (pool_recycle=1800)
#   بعد: يستورد engine الموحّد من uow.py
#
# ما بقي كما هو:
#   - Base(DeclarativeBase) — لم يُستورد من الخارج، موجود للتوافق
#   - SessionLocal — مُعاد توجيهه لـ uow.py's SessionLocal
#   - get_session() — يستخدم SessionLocal الموحّد
#
# المعيار: نقطة واحدة للـ engine = pool واحد = لا استنزاف للـ connections.

import logging
from sqlalchemy.orm import DeclarativeBase, Session

# [STEP-1A] استيراد من المصدر الوحيد بدلاً من create_engine() مستقل
from capitalguard.infrastructure.db.uow import engine, SessionLocal  # noqa: F401

log = logging.getLogger(__name__)

log.debug("[STEP-1A] base.py: engine imported from uow.py (no second engine created).")


# ─────────────────────────────────────────────────────────────────────────────
# Base — محلياً للتوافق مع أي كود يستورد Base من هنا
# ─────────────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """
    Base class للتوافق.
    ملاحظة: النماذج الفعلية تستخدم Base من models/base.py.
    هذا الـ Base موجود للتوافق مع أي استيراد مباشر من base.py.
    """
    pass


# ─────────────────────────────────────────────────────────────────────────────
# get_session — FastAPI Depends() dependency
# ─────────────────────────────────────────────────────────────────────────────

def get_session() -> Session:
    """
    FastAPI dependency لتوفير DB session لكل request.
    يستخدم SessionLocal الموحّد من uow.py (مرتبط بالـ engine الواحد).
    """
    db = SessionLocal()
    try:
        yield db
    except Exception as e:
        log.error("get_session: rollback due to: %s", e, exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()
