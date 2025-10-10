# src/capitalguard/infrastructure/db/base.py (v2.1 - COMPLETE, FINAL & PRODUCTION-READY)
"""
Database engine setup and session management.

This version includes a custom JSON serializer to handle Decimal types,
fixing the "Decimal is not JSON serializable" error when using JSONB columns.
This is a complete, final, and production-ready file.
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
# The engine is the starting point for any SQLAlchemy application. It's the
# 'home base' for the actual DBAPI connections and dialect.
engine = create_engine(
    settings.DATABASE_URL,
    
    # Connection arguments specific to certain database drivers.
    # For SQLite, this prevents errors when multiple threads access the same connection,
    # which is common in web applications.
    connect_args={"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {},
    
    # ✅ THE CORE FIX: Pass the custom serializer to the engine.
    # This instructs SQLAlchemy's dialect (e.g., psycopg) to use our custom function
    # whenever it needs to serialize a Python object into a JSON string for the database.
    json_serializer=lambda obj: json.dumps(obj, default=_custom_json_serializer)
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