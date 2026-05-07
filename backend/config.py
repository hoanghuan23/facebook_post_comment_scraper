# Configuration file for the application
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import Optional
import os


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def normalize_database_url(database_url: str) -> str:
    """Normalize SQLite URLs so relative paths are anchored to project root."""
    if not database_url.startswith("sqlite:///"):
        return database_url

    raw_path = database_url.replace("sqlite:///", "", 1)
    if raw_path == ":memory:" or raw_path.startswith("file:"):
        return database_url

    sqlite_path = Path(raw_path)
    if not sqlite_path.is_absolute():
        sqlite_path = (PROJECT_ROOT / sqlite_path).resolve()

    return f"sqlite:///{sqlite_path.as_posix()}"


def get_database_file_path(database_url: str) -> Optional[Path]:
    """Return the SQLite DB file path when database_url points to a file."""
    if not database_url.startswith("sqlite:///"):
        return None

    raw_path = database_url.replace("sqlite:///", "", 1)
    if raw_path == ":memory:" or raw_path.startswith("file:"):
        return None

    return Path(raw_path)

class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # App info
    APP_NAME: str = "Facebook Post & Comment Scraper"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = False
    
    # Server
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_BASE_URL: str = "http://localhost:8000"
    
    # Database
    DATABASE_URL: str = "sqlite:///./data/facebook_scraper.db"
    # For PostgreSQL:
    # DATABASE_URL: str = "postgresql://user:password@localhost:5432/facebook_scraper"
    
    # JWT & Security
    SECRET_KEY: str = "your-secret-key-here-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 24 * 60  # 24 hours
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # Encryption for sensitive data
    ENCRYPTION_KEY: str = "your-encryption-key-change-in-production"
    
    # Proxy settings
    PROXY_ENABLED: bool = True
    PROXY_URL: Optional[str] = None
    PROXY_ROTATION_ENABLED: bool = True
    PROXY_ROTATION_INTERVAL: int = 5  # xoay sau 5 requests tránh bị block IP
    
    # Scraping settings
    SCRAPER_ENABLED: bool = True
    SCRAPER_MAX_WORKERS: int = 3
    SCRAPER_TIMEOUT: int = 30
    SCRAPER_RETRY_ATTEMPTS: int = 3
    SCRAPER_WRITE_DEBUG_FILES: bool = False
    SCRAPER_GROUP_PAGE_SIZE: int = 10
    SCRAPER_MAX_24H_PAGES: int = 100
    
    # Scheduler settings (APScheduler)
    SCHEDULER_ENABLED: bool = True
    
    # Task schedules (cron expressions or seconds)
    TASK_SCRAPE_NEW_POSTS_INTERVAL: int = 600  # mỗi 10 phút
    TASK_UPDATE_RECENT_METRICS_INTERVAL: int = 1800 # mỗi 30 phút
    TASK_CLEANUP_OLD_DATA_INTERVAL: int = 3600  # mỗi 1 giờ
    TASK_GENERATE_ANALYTICS_INTERVAL: int = 86400 # mỗi 24 giờ
    TASK_HEALTH_CHECK_INTERVAL: int = 60 # mỗi 1 phút
    
    # Data retention
    DATA_RETENTION_DAYS: int = 30  # xóa dữ liệu cũ hơn N ngày
    KEEP_DELETED_POSTS_DAYS: int = 7  # giữ các bài viết đã xóa trong N ngày để phân tích sau
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/app.log"
    LOG_MAX_SIZE: int = 10 * 1024 * 1024  # 10 MB
    LOG_BACKUP_COUNT: int = 5
    
    # Email (for reports)
    SMTP_ENABLED: bool = False
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM_EMAIL: Optional[str] = None
    
    # Facebook API (if using official API)
    FACEBOOK_ACCESS_TOKEN: Optional[str] = None
    FACEBOOK_APP_ID: Optional[str] = None
    FACEBOOK_APP_SECRET: Optional[str] = None
    
    # Rate limiting
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_REQUESTS: int = 100 # số lượng request tối đa trong khoảng thời gian (60s)
    RATE_LIMIT_PERIOD: int = 60  # khoảng thời gian
    
    # CORS settings
    CORS_ORIGINS: list = [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
    ]
    CORS_CREDENTIALS: bool = True
    CORS_METHODS: list = ["*"]
    CORS_HEADERS: list = ["*"]

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        return normalize_database_url(str(value))
    
    class Config:
        env_file = ".env"
        case_sensitive = True


# Create a global settings instance
settings = Settings()


# Environment-specific configurations
class DevelopmentSettings(Settings):
    """Development environment settings"""
    DEBUG: bool = True
    LOG_LEVEL: str = "DEBUG"
    # Use SQLite for development
    DATABASE_URL: str = "sqlite:///./data/facebook_scraper.db"


class ProductionSettings(Settings):
    """Production environment settings"""
    DEBUG: bool = False
    LOG_LEVEL: str = "WARNING"
    # Use PostgreSQL for production
    DATABASE_URL: str = "postgresql://user:password@localhost:5432/facebook_scraper"
    SCRAPER_MAX_WORKERS: int = 5


class TestingSettings(Settings):
    """Testing environment settings"""
    DEBUG: bool = True
    DATABASE_URL: str = "sqlite:///:memory:"
    SCHEDULER_ENABLED: bool = False
    SCRAPER_ENABLED: bool = False


def get_settings() -> Settings:
    """Get settings based on environment"""
    env = os.getenv("ENV", "development").lower()
    
    if env == "production":
        return ProductionSettings()
    elif env == "testing":
        return TestingSettings()
    else:
        return DevelopmentSettings()
