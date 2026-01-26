#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/db/base.py ---
# src/capitalguard/infrastructure/db/base.py (v2.2 - Fix for Supabase Transaction Pooler)
"""
Database engine setup and session management.

Updates:
- Added 'prepare_threshold': None to connect_args for PostgreSQL.
  This fixes the 'psycopg.errors.DuplicatePreparedStatement' error caused by
  Supabase's Transaction Pooler (PgBouncer) which does not support prepared statements.
"""

import json
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import DeclarativeBase

from capitalguard.config import settings

# --- Custom JSON Serializer ---
def _custom_json_serializer(obj):
    """
    Handles non-serializable types for JSON conversion.
    Specifically, converts Decimal objects to strings to ensure compatibility
    with database JSON/JSONB types.
    """
    if isinstance(obj, Decimal):
        # Convert Decimal to string representation, which is JSON-compatible.
        return str(obj)
    # For any other type that the default encoder can't handle, raise the standard error.
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


# --- Database Engine Creation ---
# Prepare connection arguments
connect_args = {}

if settings.DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False
else:
    # ✅ THE FIX: Disable prepared statements for PostgreSQL (Supabase Transaction Pooler)
    # Setting prepare_threshold to None tells psycopg not to use server-side prepared statements.
    connect_args["prepare_threshold"] = None

engine = create_engine(
    settings.DATABASE_URL,
    
    # Pass the configured arguments
    connect_args=connect_args,
    
    # Pass the custom serializer
    json_serializer=lambda obj: json.dumps(obj, default=_custom_json_serializer),
    
    # Recommended settings for cloud databases to prevent stale connections
    pool_pre_ping=True,
    pool_recycle=1800
)


# --- Session Management ---
# A configured "Session" class. The Session is the primary interface for
# persistence operations and is the heart of the ORM.
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


# --- Base Class for Declarative Models ---
class Base(DeclarativeBase):
    """The base class for all SQLAlchemy ORM models in this application."""
    pass


# --- Dependency for FastAPI ---
def get_session():
    """
    A dependency function for FastAPI to provide a transactional DB session to endpoints.
    This pattern ensures that a new session is created for each request and is
    properly closed afterward, preventing connection leaks.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/infrastructure/db/base.py ---