# src/capitalguard/infrastructure/db/base.py (v2.0 - PRODUCTION-READY)
"""
Database engine setup and session management.
This version includes a custom JSON serializer to handle Decimal types,
fixing the "Decimal is not JSON serializable" error when using JSONB columns.
"""

import json
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from capitalguard.config import settings

# ✅ THE FIX: Custom JSON serializer function
def _custom_json_serializer(obj):
    """
    Handles non-serializable types for JSON conversion.
    Specifically, converts Decimal objects to strings.
    """
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")

# Create the database engine
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {},
    # ✅ THE FIX: Pass the custom serializer to the engine.
    # This will be used by the dialect (psycopg) when handling JSON/JSONB.
    json_serializer=lambda obj: json.dumps(obj, default=_custom_json_serializer)
)

# Create a configured "Session" class
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