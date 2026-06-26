import uuid
from collections.abc import Generator

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_default_org_and_user(db: Session) -> tuple[uuid.UUID, uuid.UUID]:
    """Retrieve or create default organization and user for no-auth MVP operations."""
    from app.models.organization import Organization
    from app.models.user import User

    # Find or create default organization
    org = db.scalars(select(Organization).limit(1)).first()
    if not org:
        org = Organization(
            id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            name="Default Org",
        )
        db.add(org)
        db.commit()
        db.refresh(org)

    # Find or create default user
    user = db.scalars(select(User).limit(1)).first()
    if not user:
        user = User(
            id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            organization_id=org.id,
            email="default@rfparchitect.com",
            hashed_password="fake-pbkdf2-sha256-hash-for-now",
            full_name="Default Workspace User",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    return org.id, user.id
