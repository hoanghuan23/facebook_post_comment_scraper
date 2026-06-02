# Database connection and session management
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
import os

from backend.config import settings, get_database_file_path
from backend.database.models import Base


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    """Create parent directory for file-based SQLite URLs."""
    sqlite_file = get_database_file_path(database_url)
    if not sqlite_file:
        return

    abs_path = os.path.abspath(str(sqlite_file))
    parent_dir = os.path.dirname(abs_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

# Create database engine
if "sqlite" in settings.DATABASE_URL:
    # SQLite (for development/testing)
    _ensure_sqlite_parent_dir(settings.DATABASE_URL)
    is_in_memory_sqlite = ":memory:" in settings.DATABASE_URL
    sqlite_engine_kwargs = {
        "connect_args": {"check_same_thread": False},
    }
    # StaticPool is only safe/needed for in-memory SQLite.
    # File-based SQLite should not share one connection across worker threads.
    if is_in_memory_sqlite:
        sqlite_engine_kwargs["poolclass"] = StaticPool
    engine = create_engine(
        settings.DATABASE_URL,
        **sqlite_engine_kwargs,
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
    from backend.database.migrations import (
        migrate_pipeline_job_type_update_metric,
        migrate_post_metric_job_id_column,
        migrate_post_metric_scheduling_columns,
    )

    migrate_post_metric_scheduling_columns()
    migrate_pipeline_job_type_update_metric()
    migrate_post_metric_job_id_column()
    print("Database tables created/verified")


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
