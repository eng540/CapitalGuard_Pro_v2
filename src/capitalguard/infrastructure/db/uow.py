# src/capitalguard/infrastructure/db/uow.py (v25.7 - Async Fix)
"""
يوفر نطاق عمل ترانزاكشني للعمليات على قاعدة البيانات.
هذا الإصدار يتضمن إصلاحًا حرجًا للديكوراتور uow_transaction للتعامل الصحيح مع الدوال غير المتزامنة.
"""

import logging
import inspect
from functools import wraps
from contextlib import contextmanager
from typing import Callable, Any

from sqlalchemy.orm import Session
from .base import SessionLocal

log = logging.getLogger(__name__)

@contextmanager
def session_scope():
    """يوفر نطاق ترانزاكشني حول سلسلة من العمليات."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

def uow_transaction(func: Callable) -> Callable:
    """
    ديكوراتور لمعالجات Telegram الذي يوفر جلسة قاعدة بيانات (وحدة العمل).
    الإصدار المصحح: يتعامل بشكل صحيح مع الدوال المتزامنة وغير المتزامنة.
    """
    if inspect.iscoroutinefunction(func):
        # الدالة غير متزامنة (async)
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            session = SessionLocal()
            try:
                # البحث عن update و context في الوسائط
                update = None
                context = None
                
                for arg in args:
                    if hasattr(arg, 'effective_user'):  # Update object
                        update = arg
                    elif hasattr(arg, 'bot'):  # Context object
                        context = arg
                
                # تمرير الجلسة للدالة المزينة
                if 'db_session' not in kwargs:
                    kwargs['db_session'] = session
                
                result = await func(*args, **kwargs)
                session.commit()
                return result
            except Exception as e:
                log.error(f"Exception in async handler '{func.__name__}', rolling back transaction.", exc_info=True)
                session.rollback()
                raise e
            finally:
                session.close()
        return async_wrapper
    else:
        # الدالة متزامنة (sync)
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            session = SessionLocal()
            try:
                if 'db_session' not in kwargs:
                    kwargs['db_session'] = session
                
                result = func(*args, **kwargs)
                session.commit()
                return result
            except Exception as e:
                log.error(f"Exception in sync handler '{func.__name__}', rolling back transaction.", exc_info=True)
                session.rollback()
                raise e
            finally:
                session.close()
        return sync_wrapper