# File: src/capitalguard/infrastructure/db/models/base.py
# [STEP-1A] يزيل engine المستقل الثالث
#
# التغيير:
#   قبل: كان ينشئ engine مستقلاً ثالثاً مع json_serializer
#   بعد: يستورد engine الموحّد من uow.py
#
# ما بقي كما هو:
#   - Base(DeclarativeBase) — تستورده جميع النماذج
#   - SessionLocal — للتوافق مع أي استخدام مباشر
#   - get_session() — للتوافق
#
# المعيار: ملف models/base.py مسؤوليته الوحيدة تعريف Base للنماذج،
# ليست مسؤوليته إدارة الـ engine.

import json
import logging
from decimal import Decimal

from sqlalchemy.orm import DeclarativeBase, sessionmaker

# [STEP-1A] استيراد engine الموحّد — لا create_engine() ثالث
from capitalguard.infrastructure.db.uow import engine, SessionLocal  # noqa: F401

log = logging.getLogger(__name__)

log.debug("[STEP-1A] models/base.py: engine imported from uow.py (no third engine created).")


# ─────────────────────────────────────────────────────────────────────────────
# Base — تستورده جميع النماذج
# ─────────────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """
    Base class لجميع SQLAlchemy ORM models في النظام.

    جميع ملفات النماذج تستورد هذا الـ Base:
        from .base import Base

    هذا يضمن أن جميع النماذج تنتمي لنفس metadata object،
    ما يجعل Base.metadata.create_all(engine) تعمل بشكل صحيح.
    """
    pass


# ─────────────────────────────────────────────────────────────────────────────
# get_session — للتوافق مع أي استخدام قديم
# ─────────────────────────────────────────────────────────────────────────────

def get_session():
    """للتوافق — يُفضَّل استخدام get_session من infrastructure/db/base.py."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
