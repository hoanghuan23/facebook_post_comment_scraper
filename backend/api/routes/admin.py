# Admin routes
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List
from datetime import datetime, timedelta
import asyncio

from backend.database.db import get_db
from backend.database.models import User, Source, Post, Comment, PostMetric
from backend.database.crud import UserCRUD, LogCRUD, SourceCRUD, PostCRUD, get_user_stats
from backend.api.auth import get_current_admin_user

router = APIRouter()


@router.get("/scraper-status")
async def get_scraper_status(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Get current scraper status"""
    from backend.scheduler.task_scheduler import get_scheduler
    
    scheduler = get_scheduler()
    
    # Get recent task logs
    recent_logs = LogCRUD.get_recent(db, "task", limit=10)
    
    # Count errors from today
    today = datetime.utcnow().date()
    errors_today = db.query(func.count(LogCRUD)).filter(
        LogCRUD.log_level == "ERROR",
        func.date(LogCRUD.created_at) == today
    ).scalar()
    
    return {
        "scheduler_running": scheduler.running if scheduler else False,
        "jobs_count": len(scheduler.get_jobs()) if scheduler else 0,
        "active_tasks": len([j for j in scheduler.get_jobs() if scheduler else []]),
        "recent_tasks": [{"task": l.log_message, "timestamp": l.created_at} for l in recent_logs],
        "errors_today": errors_today,
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.post("/scraper-action")
async def control_scraper(
    action: str,  # start, stop, pause, resume
    task_name: str = None,
    current_user: User = Depends(get_current_admin_user),
):
    """Control scraper (start/stop/pause/resume)"""
    from backend.scheduler.task_scheduler import (
        get_scheduler, start_scheduler, stop_scheduler,
        pause_scheduler, resume_scheduler
    )
    
    scheduler = get_scheduler()
    
    try:
        if action == "start":
            start_scheduler()
            return {"status": "started", "message": "Scheduler started"}
        elif action == "stop":
            stop_scheduler()
            return {"status": "stopped", "message": "Scheduler stopped"}
        elif action == "pause":
            pause_scheduler()
            return {"status": "paused", "message": "Scheduler paused"}
        elif action == "resume":
            resume_scheduler()
            return {"status": "resumed", "message": "Scheduler resumed"}
        else:
            raise HTTPException(
                status_code=400,
                detail="Invalid action. Use: start, stop, pause, resume"
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/logs")
async def get_logs(
    limit: int = 100,
    log_level: str = None,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Get application logs"""
    from backend.database.models import ScraperLog
    
    query = db.query(ScraperLog).order_by(ScraperLog.created_at.desc())
    
    if log_level:
        query = query.filter(ScraperLog.log_level == log_level)
    
    logs = query.limit(limit).all()
    
    return {
        "count": len(logs),
        "limit": limit,
        "logs": [
            {
                "timestamp": l.created_at,
                "level": l.log_level,
                "message": l.log_message,
                "source_id": l.source_id,
            }
            for l in logs
        ]
    }


@router.get("/users")
async def list_users(
    skip: int = 0,
    limit: int = 20,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """List all users (admin only)"""
    
    users = UserCRUD.get_all(db, skip=skip, limit=limit)
    
    user_stats = []
    for user in users:
        stats = get_user_stats(db, user.id)
        user_stats.append({
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "is_active": user.is_active,
            "is_admin": user.is_admin,
            "created_at": user.created_at,
            "sources_count": stats['sources_count'],
            "posts_count": stats['posts_count'],
            "total_engagement": stats['total_engagement'],
        })
    
    return {
        "count": len(user_stats),
        "users": user_stats,
    }


@router.get("/stats")
async def get_system_stats(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Get system statistics"""
    
    user_count = db.query(func.count(User.id)).scalar()
    source_count = db.query(func.count(Source.id)).scalar()
    post_count = db.query(func.count(Post.id)).scalar()
    comment_count = db.query(func.count(Comment.id)).scalar()
    metric_count = db.query(func.count(PostMetric.id)).scalar()
    
    total_engagement = db.query(
        func.sum(Post.current_likes + Post.current_shares + Post.current_comments)
    ).scalar() or 0
    
    return {
        "total_users": user_count,
        "total_sources": source_count,
        "total_posts": post_count,
        "total_comments": comment_count,
        "total_metric_snapshots": metric_count,
        "total_engagement": total_engagement,
    }


@router.post("/tasks/{task_name}")
async def run_task_manually(
    task_name: str,
    source_id: int = None,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Manually run a scheduled task"""
    from backend.scheduler import periodic_tasks
    
    try:
        if task_name == "scrape_posts":
            asyncio.create_task(periodic_tasks.periodic_scrape_new_posts(db))
            return {"status": "queued", "task": "scrape_posts"}
        
        elif task_name == "update_metrics":
            asyncio.create_task(periodic_tasks.update_recent_post_metrics(db))
            return {"status": "queued", "task": "update_metrics"}
        
        elif task_name == "cleanup":
            asyncio.create_task(periodic_tasks.cleanup_old_data(db))
            return {"status": "queued", "task": "cleanup"}
        
        elif task_name == "analytics":
            asyncio.create_task(periodic_tasks.generate_analytics_cache(db))
            return {"status": "queued", "task": "analytics"}
        
        else:
            raise HTTPException(
                status_code=400,
                detail="Invalid task. Use: scrape_posts, update_metrics, cleanup, analytics"
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

