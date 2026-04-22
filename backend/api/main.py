# Main FastAPI application
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from datetime import datetime
from sqlalchemy import text

from backend.config import settings
from backend.database.db import get_db, init_db
from backend.utils.logger import setup_logging
from backend.scheduler.task_scheduler import start_scheduler, stop_scheduler

# Configure logging
logger = setup_logging(settings.LOG_LEVEL)

# Database initialization
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    
    # Initialize database
    try:
        init_db()
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
    
    # Start scheduler
    if settings.SCHEDULER_ENABLED:
        try:
            await start_scheduler()
            logger.info("Task scheduler started")
        except Exception as e:
            logger.error(f"Scheduler startup failed: {e}")
    
    logger.info(f"API running at {settings.API_BASE_URL}")
    logger.info(f"Database: {settings.DATABASE_URL}")
    logger.info(f"Debug mode: {settings.DEBUG}")
    if settings.PROXY_ENABLED:
        logger.info("Proxy rotation enabled")

    yield  # Application running
    
    # Shutdown
    logger.info("Shutting down...")
    if settings.SCHEDULER_ENABLED:
        await stop_scheduler()
    logger.info("Goodbye")


# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    description="Facebook Post & Comment Scraper with Analytics API",
    version=settings.APP_VERSION,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)


# Middleware: CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=settings.CORS_CREDENTIALS,
    allow_methods=settings.CORS_METHODS,
    allow_headers=settings.CORS_HEADERS,
)

# Middleware: Trusted Host
if not settings.DEBUG:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "*.example.com"],
    )


# ==================== HEALTH CHECK ====================

@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": settings.APP_VERSION,
    }


@app.get("/api/health", tags=["Health"])
async def api_health_check(db = Depends(get_db)):
    """API health check with database verification"""
    try:
        # Test database connection
        db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    return {
        "status": "ok" if db_status == "connected" else "degraded",
        "database": db_status,
        "timestamp": datetime.utcnow().isoformat(),
    }


# ==================== ROOT ENDPOINT ====================

@app.get("/", tags=["Root"])
async def root():
    """Welcome endpoint"""
    return {
        "message": f"Welcome to {settings.APP_NAME}",
        "version": settings.APP_VERSION,
        "api_docs": "/api/docs",
        "endpoints": {
            "auth": "/api/auth",
            "sources": "/api/sources",
            "posts": "/api/posts",
            "analytics": "/api/analytics",
            "health": "/health",
        }
    }


# ==================== ERROR HANDLERS ====================

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "detail": str(exc) if settings.DEBUG else "An error occurred",
        }
    )


# ==================== INCLUDE ROUTERS ====================

# Import and include routers
from backend.api.routes import auth, sources, posts, analytics, admin

app.include_router(
    auth.router,
    prefix="/api/auth",
    tags=["Authentication"],
)

app.include_router(
    sources.router,
    prefix="/api/sources",
    tags=["Sources"],
)

app.include_router(
    posts.router,
    prefix="/api/posts",
    tags=["Posts"],
)

app.include_router(
    analytics.router,
    prefix="/api/analytics",
    tags=["Analytics"],
)

app.include_router(
    admin.router,
    prefix="/api/admin",
    tags=["Admin"],
)


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "backend.api.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
