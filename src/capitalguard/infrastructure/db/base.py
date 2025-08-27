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
    """
    Dependency for FastAPI endpoints to get a DB session.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()