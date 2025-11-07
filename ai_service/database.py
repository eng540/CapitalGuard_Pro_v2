# ai_service/database.py
"""
إعداد الاتصال بقاعدة البيانات لخدمة AI.
يقرأ نفس متغير DATABASE_URL الذي يستخدمه النظام الرئيسي للاتصال بنفس قاعدة البيانات.
"""

import os
import json
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from contextlib import contextmanager
import logging

log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set for AI service")

# معالج Decimal لضمان التوافق مع JSONB
def _custom_json_serializer(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")

try:
    engine = create_engine(
        DATABASE_URL,
        json_serializer=lambda obj: json.dumps(obj, default=_custom_json_serializer)
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    log.info("AI Service database engine created successfully.")
except Exception as e:
    log.critical(f"Failed to create AI Service database engine: {e}", exc_info=True)
    raise

class Base(DeclarativeBase):
    """Base class for all ORM models in this service."""
    pass

@contextmanager
def session_scope():
    """Provide a transactional scope around a series of operations."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()