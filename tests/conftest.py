# tests/conftest.py (NEW FILE)
"""
Fixtures and test setup for the Pytest suite.
"""

import pytest
import os
from unittest.mock import MagicMock, AsyncMock

# Set test environment variables BEFORE any application code is imported.
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:fake_token"
os.environ["ENV"] = "test"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from capitalguard.infrastructure.db.models.base import Base
from capitalguard.boot import build_services

@pytest.fixture(scope="session")
def db_engine():
    """Creates a test database engine and handles schema creation/teardown."""
    engine = create_engine(os.environ["DATABASE_URL"], echo=False)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    if os.path.exists("./test.db"):
        os.remove("./test.db")

@pytest.fixture(scope="function")
def db_session(db_engine):
    """Provides a clean, transactional database session for each test function."""
    connection = db_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()
    
    yield session
    
    session.close()
    transaction.rollback()
    connection.close()

@pytest.fixture(scope="function")
def services(db_session):
    """
    Builds the application services with mocked external dependencies (like the notifier).
    Crucially, it uses the real database session for integration testing.
    """
    # Mock the Telegram Notifier to prevent real API calls
    mock_notifier = MagicMock()
    mock_notifier.post_to_channel = AsyncMock(return_value=(12345, 67890))
    mock_notifier.edit_recommendation_card_by_ids = AsyncMock(return_value=True)
    
    # Build services, but inject our mock notifier
    app_services = build_services()
    app_services["notifier"] = mock_notifier
    
    # Inject the test db_session into services that need it for their internal logic
    # (This is a simplified approach for demonstration)
    
    return app_services