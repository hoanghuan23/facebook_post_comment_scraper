# Database connection and session management
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
import os

from backend.config import settings
from backend.database.models import Base

# Create database engine
if "sqlite" in settings.DATABASE_URL:
    # SQLite (for development/testing)
    engine = create_engine(
        settings.DATABASE_URL,
        connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
        poolclass=StaticPool if "sqlite" in settings.DATABASE_URL else None,
    )
else:
    # PostgreSQL
    engine = create_engine(
        settings.DATABASE_URL,
        echo=settings.DEBUG,
        pool_size=10,
        max_overflow=20,
    )

# Create session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


def init_db():
    """Initialize database tables"""
    # Create all tables
    Base.metadata.create_all(bind=engine)
    print(f"✅ Database tables created/verified")


def get_db():
    """Dependency for getting database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db():
    """Async dependency for getting database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
