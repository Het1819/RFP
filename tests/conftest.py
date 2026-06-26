import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.base import Base

# Use the configured DATABASE_URL for tests. Transactional rollback ensures isolation.
test_engine = create_engine(settings.DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="session", autouse=True)
def setup_db_tables():
    # Ensure all tables exist in the database schema
    Base.metadata.create_all(bind=test_engine)
    yield


@pytest.fixture
def db():
    """Provides a transactional database session rolled back after each test."""
    connection = test_engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db):
    """Provides a test client with get_db overridden to use test session."""

    def _override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def mock_session_local(monkeypatch, db):
    """Mock SessionLocal to wrap test db session and reuse transaction."""

    class SafeSession:
        def __init__(self):
            pass

        def __getattr__(self, name):
            return getattr(db, name)

        def close(self):
            pass

    monkeypatch.setattr("app.core.database.SessionLocal", SafeSession)
